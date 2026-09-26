"""tmux integration via subprocess."""

import hashlib
import itertools
import logging
import os
import re
import shlex
import shutil
import subprocess
from base64 import b32encode
from collections.abc import Collection
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ... import paths
from ...config import Config
from ...launch_context import neutral_directory
from ...storage import atomic_write_text, normalize_repository_path
from ...ui_theme import DEFAULT_THEME_NAME, resolve_panel_theme
from .session_env import child_unset_names, sanitized_environ, session_scrub_names

logger = logging.getLogger(__name__)

_REPO_ID_LENGTH = 5
_SESSION_LIST_SEPARATOR = "\t"
_TMUX_TERMINAL_NAME = "tmux-256color"
_TMUX_TERMINAL_FALLBACK = "screen-256color"
# 24-bit colour for the tmux clients gitdirector starts, and only those:
# terminal-features would claim it server-wide, for the user's sessions too.
TMUX_CLIENT_FEATURES = ("-T", "RGB")
# Server-wide entries older versions set; removed only while still ours.
_LEGACY_SERVER_OPTIONS = {"terminal-features[90]": "*:RGB", "terminal-overrides[90]": "*:Tc"}
# Colour-policy opt-out gitdirector overrides on purpose. The rest of
# what gets stripped from a pane is the leak policy in
# :mod:`.session_env`; see :func:`_tmux_child_unset_names`.
_TMUX_CHILD_ENV_UNSET = ("NO_COLOR",)
# Standard color-capability advertisement, honoured by well-behaved
# terminal programs (supports-color/chalk, termenv, Rich, ...).
_TMUX_STANDARD_COLOR_ENV = {
    "COLORTERM": "truecolor",
    "FORCE_COLOR": "3",
    "CLICOLOR_FORCE": "1",
}
# Vendor-specific truecolor opt-outs. Some agents ignore the standard
# variables above and clamp their palette to 256 colors whenever $TMUX
# is set; each exposes its own escape hatch, read from the process
# environment at agent startup. Add one entry per non-conforming agent.
_TMUX_AGENT_TRUECOLOR_OPT_OUTS = {
    "CLAUDE_CODE_TMUX_TRUECOLOR": "1",  # anthropics/claude-code#36785
}
_TMUX_COLOR_ENV = {**_TMUX_STANDARD_COLOR_ENV, **_TMUX_AGENT_TRUECOLOR_OPT_OUTS}

# Wall-clock cap for a single tmux/ps invocation.
#
# tmux commands are normally instant, but a wedged or unresponsive server makes
# the client block forever. Without a cap that hang propagates: the session
# monitor thread stops polling and the Sessions tab silently freezes, and any
# foreground call takes the TUI down with it. Treat a command that exceeds this
# as a failure rather than waiting on it.
TMUX_COMMAND_TIMEOUT = 10

# The tmux client prints one of these when there is no server to talk to:
# it died mid-command (observed inside `respawn-pane`), or it exited on its
# own because its last session closed. Every session on that server is gone
# with it, so a failure carrying one of these is an authoritative "no
# sessions", not an unknown error.
_TMUX_SERVER_GONE_MARKERS = (
    "server exited unexpectedly",
    "no server running",
    "lost server",
    "error connecting to",
)


def _tmux_server_is_gone(stderr: object) -> bool:
    """Whether *stderr* from a failed tmux command says the server is gone."""
    if isinstance(stderr, bytes):
        stderr = stderr.decode(errors="replace")
    if not isinstance(stderr, str):
        return False
    lowered = stderr.lower()
    return any(marker in lowered for marker in _TMUX_SERVER_GONE_MARKERS)


# Distinguishes concurrent send-text buffers within a process.
# ``itertools.count`` increments atomically, so no lock is needed.
_SEND_BUFFER_COUNTER = itertools.count()


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


# What tmux prints when the machine cannot give it what a new pane needs,
# and what to tell the user instead.
_RESOURCE_FAILURES = (
    (
        (
            "Device not configured",
            "fork failed: No space left on device",
            "No more ptys",
            "openpty",
            "open terminal failed",
        ),
        "No free terminal (pty) is left: the system limit is reached. "
        "Close sessions or terminal windows you no longer need, then try again.",
    ),
    (
        ("Resource temporarily unavailable",),
        "The limit on running processes is reached. "
        "Close programs you no longer need, then try again.",
    ),
    (
        ("Too many open files",),
        "Too many files are open. Close programs you no longer need, then try again.",
    ),
    (
        ("server exited unexpectedly", "lost server"),
        "tmux stopped while the session was starting. Try again.",
    ),
)


def explain_tmux_failure(error: BaseException | str) -> str:
    """Why tmux could not start something, in words the user can act on.

    Resource exhaustion (no free pty: ``Device not configured`` on macOS,
    ``No space left on device`` on Linux; the process limit) gets a plain
    explanation; anything else is passed on as tmux said it.
    """
    text = str(error)
    # A failed subprocess keeps tmux's own words in stderr, not in its message.
    stderr = getattr(error, "stderr", None)
    if isinstance(stderr, bytes):
        stderr = stderr.decode(errors="replace")
    if isinstance(stderr, str) and stderr.strip() and stderr.strip() not in text:
        text = f"{text}: {stderr.strip()}"
    for markers, explanation in _RESOURCE_FAILURES:
        if any(marker in text for marker in markers):
            return explanation
    return text


def _run_tmux(
    args: list[str],
    *,
    check: bool = False,
    capture_output: bool = True,
    text: bool = False,
    input: str | None = None,
    timeout: float | None = TMUX_COMMAND_TIMEOUT,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run one tmux command. Every tmux invocation goes through here.

    A hung server surfaces as a failed result (or :class:`TmuxError` with
    *check*) instead of blocking forever, and the client always starts from a
    sanitized environment.
    """
    command = ["tmux", *args]
    kwargs: dict[str, object] = {}
    if capture_output:
        kwargs["capture_output"] = True
    if text:
        kwargs["text"] = True
    if input is not None:
        kwargs["input"] = input
    # A tmux client that ends up starting the server hands it its own
    # environment, and that snapshot becomes the baseline for every pane
    # created afterwards. Sanitizing here keeps a gitdirector-started
    # server clean from birth.
    kwargs["env"] = {**sanitized_environ(), **(extra_env or {})}
    # A server this client forks keeps the client's working directory for
    # its whole life, and it is readable from inside every session.
    kwargs["cwd"] = neutral_directory()
    if timeout is not None:
        kwargs["timeout"] = timeout
    try:
        result = subprocess.run(command, **kwargs)
    except subprocess.TimeoutExpired as exc:
        # Callers branch on returncode, so surface a hung server as an ordinary
        # failure instead of an exception they are not written to handle.
        if check:
            raise TmuxError("tmux command timed out", args_list=command, returncode=None) from exc
        empty: str | bytes = "" if text else b""
        return subprocess.CompletedProcess(command, 1, empty, empty)
    except (OSError, subprocess.SubprocessError) as exc:
        raise TmuxError(str(exc), args_list=command) from exc
    if check and isinstance(result.returncode, int) and result.returncode != 0:
        raise TmuxError(
            "tmux command failed",
            args_list=command,
            returncode=result.returncode,
            stderr=result.stderr,
        )
    return result


def _chain_tmux_commands(commands: list[list[str]]) -> list[str]:
    """Join several tmux commands into one invocation's arguments.

    No argument may end in ``;``: tmux would read it as a separator.
    """
    args: list[str] = []
    for command in commands:
        if args:
            args.append(";")
        args.extend(command)
    return args


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
    digest = hashlib.sha1(str(normalized_path).encode("utf-8"), usedforsecurity=False).digest()
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
    information is lost; only the session-name label is normalized.
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


def _is_work_session(session_name: str) -> bool:
    """A ``gd/<repo>/<purpose>/<n>`` session: what decks and panels group views with."""
    return _parse_gd_session_name(session_name) is not None


def _kill_session_args(session_name: str) -> list[str]:
    """``kill-session`` for *session_name*; a work session takes its views with it."""
    group = ["-g"] if _is_work_session(session_name) else []
    return ["kill-session", *group, "-t", f"={session_name}"]


def view_attach_command(tmux: str, session_name: str, view: str, *options: str) -> str:
    """Shell for a pane's client to show *session_name* through a new view *view*.

    The view is created detached, then attached: ``new-session`` attaching a
    client whose terminal has just gone (its pane was killed) makes tmux 3.7c
    exit on ``tcgetattr failed``, taking every session with it, where
    ``attach-session`` only fails. ``destroy-unattached`` goes on last, once
    attached (set on a detached session it destroys it at once), and a view
    whose attach failed is removed. *view* is a gitdirector-made name and is
    double-quoted, so a ``$$`` in it expands in the pane's shell. *options*
    are ``name value`` pairs for the view.
    """
    target = f'"={view}:"'
    settings = [f"set-option -t {target} {option}" for option in ("status off", *options)]
    chain = r" \; ".join(
        [
            f'new-session -d -t {shlex.quote(f"={session_name}")} -s "{view}"',
            *settings,
            f'attach-session -t "={view}"',
            f"set-option -t {target} destroy-unattached on",
        ]
    )
    client = f"{tmux} {shlex.join(TMUX_CLIENT_FEATURES)}"
    return f'{client} {chain} || {tmux} kill-session -t "={view}" >/dev/null 2>&1'


def guard_session_window(session_name: str) -> None:
    """Keep a work session's window from ever closing on its own.

    Decks and panels show a session through views grouped with it, and tmux
    3.7c can segfault, taking every session down, when a window of a session
    group closes on its own (its program exits or is killed):
    ``server_kill_window`` destroys the whole group while still walking the
    session list (fixed upstream after 3.7c). With ``remain-on-exit`` the
    pane stays, dead, instead, and the ``pane-died`` hook removes the session
    and its views with ``kill-session -g``, a path that is safe. The hook
    names its session: a bare ``kill-session`` in a hook acts on whichever
    session tmux calls current, which can be a panel's.

    Both options are per window, so a pane the user split off dies the same
    way: while other panes are left, the hook only removes the dead one,
    which never closes the window.
    """
    window = f"={session_name}:{_FIRST_WINDOW}"
    kill_group = shlex.join(_kill_session_args(session_name))
    hook = (
        f"if-shell -F '#{{==:#{{window_panes}},1}}' '{kill_group}' "
        """'run-shell -C "kill-pane -t #{hook_pane}"'"""
    )
    _run_tmux(
        _chain_tmux_commands(
            [
                ["set-window-option", "-t", window, "remain-on-exit", "on"],
                ["set-hook", "-w", "-t", window, "pane-died", hook],
            ]
        ),
        check=True,
    )


# gitdirector creates its own sessions with one window, which is window 0
# only under the default base-index; "^" names the lowest window whatever
# the user's tmux.conf sets.
_FIRST_WINDOW = "^"


def _active_pane_target(session_name: str) -> str:
    """Return the exact-match tmux target for the session's active pane."""
    return f"={session_name}:"


def _session_option_target(session_name: str) -> str:
    """Return the exact-match tmux target for session-scoped options and queries."""
    return f"={session_name}:"


def _detached_session_size_args() -> list[str]:
    cols, lines = shutil.get_terminal_size()
    return ["-x", str(cols), "-y", str(lines)]


def _tmux_child_unset_names() -> tuple[str, ...]:
    """Every variable stripped from a pane gitdirector spawns a command in.

    Combines the colour-policy opt-out with the launch-context leak
    policy. Order is stable and duplicates are collapsed so the generated
    shell fragments stay comparable between runs.
    """
    return tuple(dict.fromkeys((*_TMUX_CHILD_ENV_UNSET, *child_unset_names())))


def _tmux_child_env() -> dict[str, str]:
    """What every pane gitdirector spawns a command in is given."""
    env = {"TERM": _default_terminal(), **_TMUX_COLOR_ENV}
    override = paths.home_override()
    if override:
        # gitdirector run inside a session must find the same folder.
        env[paths.HOME_ENV_VAR] = str(paths.home_dir())
    return env


def _tmux_child_environment_prefix() -> str:
    unset_args = " ".join(f"-u {shlex.quote(name)}" for name in _tmux_child_unset_names())
    set_args = " ".join(f"{name}={shlex.quote(value)}" for name, value in _tmux_child_env().items())
    return f"{unset_args} {set_args}".strip()


def _tmux_child_environment_command(command: str) -> str:
    return f"env {_tmux_child_environment_prefix()} {command}"


def _tmux_new_session_environment_args() -> list[str]:
    args: list[str] = []
    for name, value in _tmux_child_env().items():
        args.extend(["-e", f"{name}={value}"])
    return args


def _tmux_global_environment_names() -> tuple[str, ...]:
    """Variable names currently in the tmux server's global environment."""
    result = _run_tmux(["show-environment", "-g"], text=True)
    if result.returncode != 0 or not result.stdout:
        return ()
    names: list[str] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        # tmux prints "-NAME" for variables marked for removal and
        # "NAME=value" for the rest.
        name = line[1:] if line.startswith("-") else line.partition("=")[0]
        if name:
            names.append(name)
    return tuple(names)


def _scrub_session_environment(session_name: str) -> tuple[str, ...]:
    """Remove gitdirector's launch context from *session_name*'s environment.

    A pane's environment is the tmux server's global environment merged
    with its session's, and the global one belongs to whichever process
    started the server -- often ``gd`` itself, sometimes a tmux the user
    started hours earlier from an entirely different directory. Session
    scope overrides global scope, so removing the names here is what
    actually guarantees the isolation, regardless of how the server came
    to exist or what it was holding.

    The server's live environment is inspected as well as the static
    policy list, so prefix rules catch variables gitdirector never saw in
    its own environment. Returns the names that were removed.
    """
    names = session_scrub_names(_tmux_global_environment_names())
    if not names:
        return ()
    target = _session_option_target(session_name)
    args = _chain_tmux_commands([["set-environment", "-r", "-t", target, name] for name in names])
    result = _run_tmux(args)
    if isinstance(result.returncode, int) and result.returncode != 0:
        raise TmuxError(
            "tmux set-environment failed while isolating the session environment",
            args_list=["tmux", *args],
            returncode=result.returncode,
            stderr=result.stderr,
        )
    return names


# What ``#{pane_pid}`` reads for a pane whose last respawn could not fork.
_BROKEN_PANE_PID = "-1"


def respawn_pane(target: str, command: str | None = None) -> None:
    """Replace *target*'s program with ``respawn-pane -k``, at most once.

    A respawn that cannot fork (no free pty, process limit) leaves the pane
    with no process and, in tmux before 3.8, no input context; the next
    ``respawn-pane`` on it dereferences that NULL and the server segfaults,
    taking every session with it. So a failed respawn is never retried and
    its pane is killed, and a pane already left broken is killed rather
    than respawned. Either way this raises :class:`TmuxError`.
    """
    probe = _run_tmux(["display-message", "-p", "-t", target, "#{pane_pid}"], text=True)
    if probe.returncode == 0 and probe.stdout.strip() == _BROKEN_PANE_PID:
        _run_tmux(["kill-pane", "-t", target])
        raise TmuxError(f"pane {target} was left broken by a failed respawn; killed it")
    args = ["respawn-pane", "-k", "-t", target, *([command] if command is not None else [])]
    result = _run_tmux(args, text=True)
    if isinstance(result.returncode, int) and result.returncode != 0:
        _run_tmux(["kill-pane", "-t", target])
        raise TmuxError(
            "tmux respawn-pane failed",
            args_list=["tmux", *args],
            returncode=result.returncode,
            stderr=result.stderr,
        )


def _respawn_session_shell(session_name: str) -> None:
    """Restart the session's first shell so it picks up the scrubbed environment.

    ``new-session`` spawns its shell before gitdirector has a chance to
    remove anything, so that first shell still holds whatever leaked.
    Respawning with no command reuses tmux's ``default-command`` /
    ``default-shell`` and the pane's original start directory, so the
    result is indistinguishable from a freshly created shell -- minus the
    leak. Nothing has been typed into the pane at this point, so the
    restart is invisible.
    """
    respawn_pane(_active_pane_target(session_name))


def _session_pane_path(session_name: str) -> str | None:
    """Return the working directory of *session_name*'s active pane."""
    result = _run_tmux(
        [
            "display-message",
            "-p",
            "-t",
            _active_pane_target(session_name),
            "#{pane_current_path}",
        ],
        text=True,
    )
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout.strip() or None


def _same_directory(left: str | Path, right: str | Path) -> bool:
    # By identity: on a case-insensitive file system tmux reports the case
    # on disk, which need not be the case the path was registered with.
    try:
        return os.path.samefile(left, right)
    except OSError:
        pass
    try:
        return os.path.realpath(str(left)) == os.path.realpath(str(right))
    except OSError:
        return False


def _tmux_color_environment_config(quoted_session: str) -> list[str]:
    lines = [
        f"set-environment -r -t {quoted_session} {shlex.quote(name)}"
        for name in _tmux_child_unset_names()
    ]
    lines.extend(
        f"set-environment -t {quoted_session} {name} {shlex.quote(value)}"
        for name, value in _TMUX_COLOR_ENV.items()
    )
    return lines


@lru_cache(maxsize=1)
def _default_terminal() -> str:
    """``tmux-256color`` where this machine has its terminfo entry, else the
    universally available ``screen-256color``."""
    try:
        probe = subprocess.run(
            ["infocmp", _TMUX_TERMINAL_NAME],
            capture_output=True,
            timeout=TMUX_COMMAND_TIMEOUT,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return _TMUX_TERMINAL_FALLBACK
    return _TMUX_TERMINAL_NAME if probe.returncode == 0 else _TMUX_TERMINAL_FALLBACK


def _tmux_terminal_capability_config(quoted_session: str) -> list[str]:
    legacy_cleanup = [
        f"if-shell -F '#{{==:#{{{option}}},{value}}}' \"set-option -gu '{option}'\""
        for option, value in _LEGACY_SERVER_OPTIONS.items()
    ]
    return [*legacy_cleanup, *_tmux_color_environment_config(quoted_session)]


def list_repo_sessions(repo_name: str | Path) -> list[str]:
    """List all tmux sessions for a given repository.

    A path matches its own sessions only; a bare name matches the sessions
    of every repository with that name.
    """
    if isinstance(repo_name, Path):
        prefix = f"gd/{_repo_session_name_segment(repo_name)}/"
    else:
        prefix = f"gd/{_sanitize_repo_name(repo_name)}_"
    return sorted(
        session_name
        for session_name in _list_sessions()
        if session_name.startswith(prefix) and _parse_gd_session_name(session_name) is not None
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
        entry = session_entry(session_name, repo_label, description)
        if entry is not None:
            entries.append(entry)
    return entries


def session_entry(session_name: str, repo_label: str, description: str) -> dict[str, str] | None:
    """Build the Sessions-tab entry for one ``gd/*`` session, or None for others."""
    parsed = _parse_gd_session_name(session_name)
    if parsed is None:
        return None
    repo_slug, purpose, _ = parsed
    repo_label = repo_label.strip()
    if not repo_label or "\n" in repo_label or "/" in repo_label:
        repo_label = _repo_label_from_segment(repo_slug)
    return {
        "session_name": session_name,
        "repo": repo_label,
        "repo_slug": repo_slug,
        "purpose": purpose,
        "description": description.strip() or GD_DEFAULT_DESCRIPTION,
    }


def create_tmux_session(
    repo_name: str,
    path: Path,
    purpose: str = "shell",
    *,
    description: str | None = None,
    repo_label: str | None = None,
    shell: bool = True,
) -> str:
    """Create a new detached tmux session with a unique name and return it.

    The session is isolated from gitdirector's own launch context: its
    working directory is *path* and nothing else, and the variables
    describing where and how ``gd`` was started are removed before the
    session's shell is allowed to run. See :mod:`.session_env`.

    The optional *description* is stored in the session's
    ``@gitdirector_description`` tmux option so it can be displayed in
    the TUI Sessions tab. A value of ``None`` or ``""`` leaves the option
    unset (the next read returns the default ``"-"`` placeholder).

    Pass ``shell=False`` when the pane's program is replaced right after
    (:func:`~.monitor.launch_command_in_tmux_session`): the first shell is
    then left for that respawn to end instead of being respawned twice.

    Raises :class:`TmuxError` when *path* is not a directory. tmux itself
    would not: ``new-session -c`` on a missing directory exits 0 and
    silently starts the session in ``$HOME``, so a repository that has
    been moved or deleted would otherwise hand an agent the user's home
    directory while still labelling the session with the repo's name.
    """
    if not path.is_dir():
        raise TmuxError(f"repository path is not a directory: {path}")

    sessions = _list_sessions()
    environment_args = _tmux_new_session_environment_args()
    max_attempts = 5
    for _attempt in range(max_attempts):
        session_name = _make_session_name(repo_name, purpose, repo_path=path, sessions=sessions)
        result = _run_tmux(
            [
                "new-session",
                "-d",
                *environment_args,
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
        guard_session_window(session_name)
        # Order matters: scrub first so the removals are in place, then
        # respawn the shell that new-session already started with the
        # unscrubbed environment.
        _scrub_session_environment(session_name)
        if shell:
            _respawn_session_shell(session_name)

        pane_path = _session_pane_path(session_name)
        if pane_path is not None and not _same_directory(pane_path, path):
            raise TmuxError(
                f"tmux session started in {pane_path} instead of the repository path {path}"
            )

        if repo_label is not None and repo_label.strip():
            _set_session_repo_label(session_name, repo_label)
        if description is not None and description.strip():
            _set_session_description(session_name, description)
        try_sync_panel_tmux_config()
        return session_name
    except Exception:
        kill_tmux_session(session_name)
        raise


def kill_tmux_session(session_name: str) -> bool:
    """Kill a tmux session by its **full exact name**. Returns True on success.

    The argument MUST be a complete session name (e.g. ``gd/repo/shell/1``).
    Anything else is rejected with ``ValueError``: partial names, glob
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
        result = _run_tmux(_kill_session_args(session_name))
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


def kill_all_gd_sessions() -> list[str]:
    """Kill every ``gd/*`` tmux session and return the names that were killed.

    Uses :func:`list_all_gd_sessions` plus persistent panel sessions to
    enumerate, then forwards each name through :func:`kill_tmux_session` so
    the same exact-match and ``gd/`` prefix guarantees apply. Names that fail
    to kill (already gone, server crashed mid-iteration) are silently skipped;
    this is best-effort cleanup, not a hard guarantee, so a partially-stale
    list is acceptable.

    Returns the list of session names that were successfully killed, which
    callers can use to print a summary. Returns an empty list when tmux is
    not running or no ``gd/*`` sessions exist.
    """
    try:
        entries = list_all_gd_sessions()
        session_names = {entry["session_name"] for entry in entries}
        session_names.update(
            session_name
            for session_name in _list_sessions()
            if _is_persistent_panel_session(session_name) or _is_helper_session(session_name)
        )
    except (TmuxError, OSError, ValueError):
        logger.debug("Failed to enumerate GitDirector tmux sessions", exc_info=True)
        return []

    killed: list[str] = []
    for session_name in sorted(session_names):
        try:
            if kill_tmux_session(session_name):
                killed.append(session_name)
        except ValueError:
            logger.debug("Refusing to kill unexpected session name during reset: %s", session_name)
            continue
    return killed


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


def _sidebar_enabled() -> bool:
    try:
        return Config().sidebar
    except Exception:
        logger.debug("could not read the sidebar setting", exc_info=True)
        return True


def _opens_in_deck(session_name: str) -> bool:
    """Repository sessions open beside the session sidebar unless it is turned off."""
    return _parse_gd_session_name(session_name) is not None and _sidebar_enabled()


def prepare_attach(session_name: str) -> str | None:
    """Build the deck *session_name* will open in, or None when it attaches directly.

    Callers that give up the terminal to attach (the console) build it
    first, so the screen goes straight from them to the finished deck.
    """
    if not _opens_in_deck(session_name) or not _session_exists(session_name):
        return None
    from .deck import open_deck

    return open_deck(session_name)


# Suffix of the terminal description a tmux attach client is given; see
# attach_client_env.
_SAME_SCREEN_SUFFIX = "-gdscreen"
_same_screen_env: dict[str, str] | None = None


# Synchronized output: the terminal keeps showing its last frame until the
# console ends it.
_HOLD_DISPLAY = b"\033[?2026h"


def _terminfo_string(value: bytes) -> str:
    out = []
    for byte in value:
        char = chr(byte)
        if byte == 0x1B:
            out.append("\\E")
        elif char in ",:^\\" or not 0x20 < byte < 0x7F:
            out.append(f"\\{byte:03o}")
        else:
            out.append(char)
    return "".join(out)


def _terminfo_compiled(directory: Path, name: str) -> bool:
    first = name[0]
    return (directory / first / name).exists() or (directory / f"{ord(first):x}" / name).exists()


def attach_client_env() -> dict[str, str]:
    """Environment for a ``tmux attach`` that draws on the screen it is handed.

    tmux switches the terminal to its alternate screen and back around a
    client (the terminal's ``smcup``/``rmcup``), and the way back shows the
    shell's own screen for an instant before the console redraws. A copy of
    the terminal's description without those two strings, compiled once per
    run into ``~/.gitdirector/terminfo``, lets the console hand its screen to
    tmux and take it back directly.

    On its way out tmux also clears the screen and prints ``[detached ...]``.
    ``rmkx``, which tmux sends only then and just before the clear, also
    starts synchronized output, so the deck's last frame stays up until the
    console has drawn over it and ends the hold. Everything else is the
    terminal's own entry (``use=``); when it cannot be built, the attach runs
    as before.
    """
    global _same_screen_env
    if _same_screen_env is not None:
        return _same_screen_env
    _same_screen_env = {}
    base = os.environ.get("TERM", "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", base) or base.endswith(_SAME_SCREEN_SUFFIX):
        return _same_screen_env
    name = base + _SAME_SCREEN_SUFFIX
    directory = paths.cache_dir() / "terminfo"
    source = directory / f"{name}.src"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        leave_keypad = subprocess.run(
            ["tput", "-T", base, "rmkx"], capture_output=True, timeout=10, check=False
        ).stdout
        source.write_text(
            f"{name}|{base} drawing on the screen it is given (gitdirector),\n"
            f"\tsmcup@, rmcup@, rmkx={_terminfo_string(leave_keypad + _HOLD_DISPLAY)},\n"
            f"\tuse={base},\n"
        )
        result = subprocess.run(
            ["tic", "-x", "-o", str(directory), str(source)],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        logger.debug("could not build the attach terminal description", exc_info=True)
        return _same_screen_env
    finally:
        source.unlink(missing_ok=True)
    if result.returncode != 0 or not _terminfo_compiled(directory, name):
        logger.debug("tic failed for %s: %s", name, result.stderr)
        return _same_screen_env
    dirs = [str(directory), *(d for d in os.environ.get("TERMINFO_DIRS", "").split(":") if d)]
    # An empty entry keeps ncurses' own default locations in the search.
    _same_screen_env = {"TERM": name, "TERMINFO_DIRS": ":".join([*dirs, ""])}
    return _same_screen_env


def attach_tmux_session(
    session_name: str, *, skip_config_sync: bool = False, deck: str | None = None
) -> bool:
    """Attach to *session_name*, blocking until the client detaches or the session ends.

    Returns ``False`` when running inside tmux, where the current client is
    switched to the session instead and the call returns at once.

    A repository session opens in a deck, beside the session sidebar (see
    :mod:`.deck`); the client then attaches to the deck, and this blocks
    until it leaves it.

    When *skip_config_sync* is true the theme sync is left out; callers that
    just created the session (and therefore synced moments ago) set it.
    *deck* is one :func:`prepare_attach` already built for the session.
    """
    if not _session_exists(session_name):
        raise TmuxError(f"tmux session no longer exists: {session_name}")
    if deck is not None or _opens_in_deck(session_name):
        from .deck import attach_deck

        return attach_deck(session_name, deck)
    if not skip_config_sync and session_name.startswith("gd/"):
        try_sync_panel_tmux_config()
    if _is_persistent_panel_session(session_name):
        from .panels import _ensure_panel_prefix_bindings

        _ensure_panel_prefix_bindings()
        reflow_panel_tmux_session(session_name)
    if os.environ.get("TMUX"):
        _run_tmux(["switch-client", "-t", f"={session_name}"], check=True, capture_output=False)
        return False
    # The attach client blocks for the entire interactive session, so it
    # must never inherit the default command timeout: a timeout here
    # SIGKILLs the tmux client mid-session, which the user experiences as a
    # random detach with the terminal left in tmux's alternate screen.
    result = _run_tmux(
        [*TMUX_CLIENT_FEATURES, "attach-session", "-t", f"={session_name}"],
        capture_output=False,
        timeout=None,
        extra_env=attach_client_env(),
    )
    # A non-zero exit is not necessarily a failed attach. The client also
    # exits 1 after a perfectly normal interactive session when the server
    # went away underneath it ("[server exited]", "[lost server]"), or when
    # the session vanished in the moment before this call ("can't find
    # session"). Every one of those means the session is over, which is
    # exactly what the caller waits for. Only an attach that fails while its
    # target is still alive is a real error.
    if result.returncode != 0 and _session_exists(session_name):
        raise TmuxError(
            "tmux attach-session failed",
            args_list=list(result.args),
            returncode=result.returncode,
        )
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
    digest = hashlib.sha1(name.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]
    return f"panel-{digest}"


def _is_persistent_panel_session(session_name: str) -> bool:
    parts = session_name.split("/")
    return len(parts) == 3 and parts[:2] == ["gd", "panel"] and bool(parts[2])


def _is_deck_session(session_name: str) -> bool:
    parts = session_name.split("/")
    return len(parts) == 3 and parts[:2] == ["gd", "deck"] and bool(parts[2])


def _is_helper_session(session_name: str) -> bool:
    """A view of a session, a panel still being built, or a deck."""
    return session_name.startswith(("gd/view/", "gd/build/", "gd/deck/"))


def make_panel_session_name(panel_name: str) -> str:
    return f"gd/panel/{_sanitize_panel_name(panel_name)}"


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
    args = ["capture-pane", "-p", "-t", _active_pane_target(session_name)]
    if full:
        args.extend(["-S", "-"])
    elif lines is not None and lines > 0:
        args.extend(["-S", f"-{lines}"])
    result = _run_tmux(args, text=True)
    if result.returncode != 0:
        return None
    if full or lines is None or lines <= 0:
        return result.stdout
    # ``-S -N`` is N history lines *plus* the whole visible screen, whose
    # bottom is usually blank rows below the prompt.
    tail = result.stdout.rstrip("\n").splitlines()[-lines:]
    return "\n".join(tail) + "\n" if tail else ""


@dataclass(frozen=True)
class ScreenCapture:
    """The visible screen of a pane: one line per row, with SGR colour escapes."""

    lines: list[str]
    width: int
    height: int
    #: ``(column, row)`` of the cursor, or None when the program hides it.
    cursor: tuple[int, int] | None


def capture_screen(session_name: str) -> ScreenCapture | None:
    """The visible screen of *session_name*'s active pane, or None when it is gone."""
    if not _session_exists(session_name):
        return None
    target = _active_pane_target(session_name)
    # One invocation, so the cursor and the screen come from the same moment.
    # -N keeps trailing spaces, which carry the background of coloured rows.
    result = _run_tmux(
        _chain_tmux_commands(
            [
                [
                    "display-message",
                    "-p",
                    "-t",
                    target,
                    "#{pane_width} #{pane_height} #{cursor_x} #{cursor_y} #{cursor_flag}",
                ],
                ["capture-pane", "-p", "-e", "-N", "-t", target],
            ]
        ),
        text=True,
    )
    if result.returncode != 0:
        return None
    geometry, _, content = result.stdout.partition("\n")
    try:
        width, height, cursor_x, cursor_y, cursor_flag = map(int, geometry.split())
    except ValueError:
        return None
    lines = content.split("\n")[:height]
    return ScreenCapture(
        lines=lines + [""] * (height - len(lines)),
        width=width,
        height=height,
        cursor=(cursor_x, cursor_y) if cursor_flag else None,
    )


def send_key_to_session(session_name: str, key: str) -> bool:
    if not _session_exists(session_name):
        return False
    result = _run_tmux(["send-keys", "-t", _active_pane_target(session_name), key], text=True)
    return result.returncode == 0


def send_text_to_session(session_name: str, text: str, *, enter: bool = False) -> bool:
    if not _session_exists(session_name):
        return False
    if not text:
        # tmux refuses to load an empty buffer.
        return send_key_to_session(session_name, "Enter") if enter else True

    # The buffer is a server-wide named slot, so a pid-only name collides
    # whenever two sends overlap -- the second load overwrites the first, and
    # both panes get the same text while one paste fails on a deleted buffer.
    # The counter makes each send own its own buffer.
    buffer_name = f"gitdirector-send-{os.getpid()}-{next(_SEND_BUFFER_COUNTER)}"
    load_result = _run_tmux(["load-buffer", "-b", buffer_name, "-"], input=text, text=True)
    if load_result.returncode != 0:
        return False

    # -p: bracketed paste, so a program that asks for it (agents, shells)
    # gets multi-line text as one paste instead of an Enter per line.
    paste_result = _run_tmux(
        ["paste-buffer", "-p", "-b", buffer_name, "-t", _active_pane_target(session_name)],
        text=True,
    )
    _run_tmux(["delete-buffer", "-b", buffer_name], text=True)
    if paste_result.returncode != 0:
        return False
    if enter:
        return send_key_to_session(session_name, "Enter")
    return True


GD_DESCRIPTION_OPTION = "@gitdirector_description"
GD_REPO_LABEL_OPTION = "@gitdirector_repo_label"
GD_DEFAULT_DESCRIPTION = "-"


def _set_session_option(session_name: str, option: str, value: str | None) -> None:
    """Set a session-scoped tmux option, or unset it when *value* is None."""
    target = _session_option_target(session_name)
    if value is None:
        _run_tmux(["set-option", "-u", "-t", target, option])
    else:
        _run_tmux(["set-option", "-t", target, option, value])


def _set_session_repo_label(session_name: str, repo_label: str) -> None:
    clean = " ".join((repo_label or "").split())
    if clean:
        _set_session_option(session_name, GD_REPO_LABEL_OPTION, clean)


def _get_session_description(session_name: str) -> str:
    """Return the user-set description for *session_name*.

    Reads the ``@gitdirector_description`` session option. Returns the
    default placeholder ("-") when the option is unset or the session is
    unavailable.
    """
    result = _run_tmux(
        [
            "show-option",
            "-t",
            _session_option_target(session_name),
            "-v",
            "-q",
            GD_DESCRIPTION_OPTION,
        ],
        text=True,
    )
    if result.returncode != 0:
        return GD_DEFAULT_DESCRIPTION
    return result.stdout.strip() or GD_DEFAULT_DESCRIPTION


def _set_session_description(session_name: str, description: str) -> None:
    """Set the user-facing description for *session_name* in tmux.

    Pass an empty string to clear the description (the next read will
    return the default "-" placeholder). The change is applied directly
    to the live session; ``sync_panel_tmux_config`` does not need to be
    called.
    """
    # The monitor reads it back in a tab-separated, line-per-pane listing.
    clean = " ".join((description or "").split())
    _set_session_option(session_name, GD_DESCRIPTION_OPTION, clean or None)


def _panel_session_label(session_name: str | None) -> str | None:
    parsed = _parse_gd_session_name(session_name)
    if parsed:
        repo_slug, purpose, sequence = parsed
        return f"{purpose} {_repo_label_from_segment(repo_slug)}/{sequence}"
    return _session_slug(session_name)


def _panel_pane_title(session_name: str | None) -> str:
    return _panel_session_label(session_name) or "empty"


def _resolved_panel_theme_name(theme_name: str | None = None) -> str:
    if theme_name:
        return theme_name
    configured_theme = Config().theme
    if configured_theme:
        return configured_theme
    return DEFAULT_THEME_NAME


#: Session option a panel's view of a session carries: the slot it fills.
PANEL_SLOT_OPTION = "@gd_slot"


def _session_header_format(session_name: str, theme_name: str | None = None) -> str:
    """The top border of a gitdirector session: its label, and a slot badge in panels.

    tmux draws a border for each client viewing the window, with that
    client's session, so the badge appears only where the session is seen
    through a panel's view session.
    """
    theme = resolve_panel_theme(_resolved_panel_theme_name(theme_name))
    slot = f"#{{{PANEL_SLOT_OPTION}}}"
    label = _panel_pane_title(session_name).replace("#", "##")
    badge = (
        f"#{{?{slot},#[bold fg={theme.badge_active_fg} bg={theme.badge_active_bg}] {slot} "
        "#[default],}"
    )
    return f"{badge}#[fg={theme.label_active_fg} bg={theme.label_active_bg}] {label} #[default]"


def _panel_window_status_format() -> str:
    return " #{pane_index}:#{pane_title} "


def _tmux_design_config_path() -> Path:
    return paths.cache_dir() / "tmux_design.conf"


def _session_badge_text(session_name: str) -> str:
    parts = session_name.split("/")
    if len(parts) >= 4 and parts[0] == "gd" and parts[1] != "panel":
        return parts[2].upper()
    return "SESSION"


def _current_window_target(session_name: str) -> str:
    result = _run_tmux(
        [
            "display-message",
            "-p",
            "-t",
            _session_option_target(session_name),
            "#{session_name}:#{window_index}",
        ],
        text=True,
    )
    if result.returncode == 0:
        target = result.stdout.strip()
        if target:
            return target
    return f"{session_name}:{_FIRST_WINDOW}"


_STATUS_BADGE_OPTION = "@gd_badge"
_STATUS_LABEL_OPTION = "@gd_label"
_STATUS_BADGE_MAX = 24
# The label gets what the badge, window list and clock leave of the width.
_STATUS_LABEL_WIDTH = "#{?#{e|>:#{window_width},70},#{e|-:#{window_width},58},12}"


def _tmux_theme_config(
    badge_text: str | None,
    label_text: str | None,
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
    status_left: str | None = None,
) -> str:
    theme = resolve_panel_theme(_resolved_panel_theme_name(theme_name))
    window_target = window_target or f"{session_name}:{_FIRST_WINDOW}"
    quoted_session = shlex.quote(_session_option_target(session_name))
    quoted_window = shlex.quote(f"={window_target}")
    status_left = status_left or (
        f"#[bold fg={theme.badge_active_fg},bg={theme.badge_active_bg}]"
        f" #{{=/{_STATUS_BADGE_MAX}/…:{_STATUS_BADGE_OPTION}}} #[default]"
        f"#[fg={theme.label_active_fg},bg={theme.label_active_bg}]"
        f" #{{=/{_STATUS_LABEL_WIDTH}/…:{_STATUS_LABEL_OPTION}}} #[default]"
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
                # Each part truncates itself with an ellipsis instead.
                f"set-option -t {quoted_session} status-left-length 1000",
                f"set-option -t {quoted_session} status-right-length 24",
                f"set-option -t {quoted_session} status-left {shlex.quote(status_left)}",
                f"set-option -t {quoted_session} status-right {shlex.quote(status_right)}",
            ]
        )
        if badge_text is not None:
            lines.append(
                f"set-option -t {quoted_session} {_STATUS_BADGE_OPTION} {shlex.quote(badge_text)}"
            )
        if label_text is not None:
            lines.append(
                f"set-option -t {quoted_session} {_STATUS_LABEL_OPTION} {shlex.quote(label_text)}"
            )
    else:
        lines.append(f"set-option -t {quoted_session} status off")

    lines.extend(
        [
            *_tmux_terminal_capability_config(quoted_session),
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
        window_target=f"{session_name}:{_FIRST_WINDOW}",
        # Every pane shows its session's own header; a panel title row
        # above it would say the same thing twice.
        pane_border_status="off",
        window_status_format=_panel_window_status_format(),
        window_status_current_format=_panel_window_status_format(),
        show_status=True,
    )


def _deck_tmux_config(session_name: str, theme_name: str | None = None) -> str:
    theme = resolve_panel_theme(_resolved_panel_theme_name(theme_name))
    config = _tmux_theme_config(
        None,
        None,
        session_name,
        theme_name,
        window_target=f"{session_name}:{_FIRST_WINDOW}",
        # The shown session draws its own header.
        pane_border_status="off",
        pane_border_lines="heavy",
        window_status_format="",
        window_status_current_format="",
        status_left=_deck_key_hints(theme),
    )
    # The sidebar's own tint shows focus, so the divider can go: tmux 3.6+
    # draws borders as spaces, which take the pane background and vanish.
    window = shlex.quote(f"={session_name}:{_FIRST_WINDOW}")
    hide = " ; ".join(
        f"set-window-option -t {window} {option}"
        for option in (
            "pane-border-lines spaces",
            'pane-border-style "fg=default,bg=default"',
            'pane-active-border-style "fg=default,bg=default"',
        )
    )
    return config + f"if-shell -F '#{{>=:#{{version}},3.6}}' {shlex.quote(hide)}\n"


# Below these client widths the deck's keys shrink, then go.
_DECK_HINTS_MIN_WIDTH = 90
_DECK_HINTS_COMPACT_MIN_WIDTH = 64


def _blend_hex(color: str, other: str, amount: float) -> str:
    a = [int(color[i : i + 2], 16) for i in (1, 3, 5)]
    b = [int(other[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * amount):02X}" for x, y in zip(a, b))


def _deck_key_hints(theme) -> str:
    """The deck's keys for the status line, drawn with the live prefix key."""
    muted = _blend_hex(theme.foreground, theme.panel, 0.45)
    # No commas: these sit inside #{?...} conditionals.
    key = f"#[fg={theme.primary}]#[bold]"
    label = f"#[nobold]#[fg={muted}]"
    full = (
        f" {key}⇥ / #{{prefix}} ⇥{label} session ↔ sidebar   "
        f"{key}#{{prefix}} b{label} toggle   "
        f"{key}#{{prefix}} d{label} console #[default]"
    )
    compact = (
        f" {key}⇥{label} sidebar  "
        f"{key}#{{prefix}} b{label} toggle  "
        f"{key}#{{prefix}} d{label} console #[default]"
    )
    wide = f"#{{e|>=:#{{client_width}},{_DECK_HINTS_MIN_WIDTH}}}"
    medium = f"#{{e|>=:#{{client_width}},{_DECK_HINTS_COMPACT_MIN_WIDTH}}}"
    return f"#{{?{wide},{full},#{{?{medium},{compact},}}}}"


def _session_tmux_config(
    session_name: str, theme_name: str | None = None, *, window_target: str | None = None
) -> str:
    header = _session_header_format(session_name, theme_name)
    quoted_session = shlex.quote(_session_option_target(session_name))
    new_window_header = (
        "set-window-option pane-border-status top ; "
        "set-window-option pane-border-lines heavy ; "
        f'set-window-option pane-border-format "{header}"'
    )
    config = _tmux_theme_config(
        _session_badge_text(session_name),
        _session_slug(session_name) or session_name,
        session_name,
        theme_name,
        window_target=window_target or _current_window_target(session_name),
        pane_border_status="top",
        pane_border_lines="heavy",
        pane_border_format=header,
    )
    extra = [
        f"set-option -t {quoted_session} status on",
        # Ending the session (an agent exiting) returns the attached client
        # to the console, whatever the user's tmux.conf prefers.
        f"set-option -t {quoted_session} detach-on-destroy on",
        f"set-hook -t {quoted_session} after-new-window {shlex.quote(new_window_header)}",
    ]
    return config + "\n".join(extra) + "\n"


def _live_session_windows() -> dict[str, str]:
    """Every live session mapped to its current window as ``session:index``."""
    result = _run_tmux(
        ["list-sessions", "-F", f"#{{session_name}}{_SESSION_LIST_SEPARATOR}#{{window_index}}"],
        text=True,
    )
    if result.returncode != 0:
        return {}
    windows: dict[str, str] = {}
    for line in result.stdout.splitlines():
        session_name, separator, window_index = line.rpartition(_SESSION_LIST_SEPARATOR)
        if separator and session_name:
            windows[session_name] = f"{session_name}:{window_index}"
    return windows


def _stored_panels() -> list:
    """The saved panels; none when the panels file cannot be read."""
    from ...commands.tui.panels import PanelStore

    try:
        return PanelStore().panels
    except (OSError, ValueError):
        logger.debug("could not read the saved panels", exc_info=True)
        return []


def _live_panel_sessions(live_sessions: Collection[str]) -> list[tuple[str, str]]:
    if not any(_is_persistent_panel_session(name) for name in live_sessions):
        return []
    return [
        (panel.name, session_name)
        for panel in _stored_panels()
        if (session_name := make_panel_session_name(panel.name)) in live_sessions
    ]


def _panel_for_session(session_name: str):
    for panel in _stored_panels():
        if make_panel_session_name(panel.name) == session_name:
            return panel
    return None


def reflow_panel_tmux_session(session_name: str) -> bool:
    """Lay the panel out exactly at its current size and (re)install its resize hook."""
    from .panels import (
        _equalize_panel_layout,
        _install_panel_resize_hook,
        _list_window_panes_row_major,
    )

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
        by_slot = _equalize_panel_layout(session_name, pane_ids[:total_panes], panel.layout)
        _install_panel_resize_hook(session_name, by_slot, panel.layout)
    except (OSError, subprocess.CalledProcessError, TmuxError, ValueError):
        return False
    return True


def sync_panel_tmux_config(theme_name: str | None = None) -> Path:
    resolved_theme = _resolved_panel_theme_name(theme_name)
    config_path = _tmux_design_config_path()
    config_path.parent.mkdir(exist_ok=True)
    windows = _live_session_windows()
    live_panel_sessions = _live_panel_sessions(windows)
    live_repo_sessions = sorted(name for name in windows if _parse_gd_session_name(name))
    live_decks = sorted(name for name in windows if _is_deck_session(name))

    lines = [
        "# Generated by GitDirector",
        f"# theme: {resolved_theme}",
        "",
    ]
    lines.extend(
        _panel_tmux_config(panel_name, session_name, resolved_theme)
        for panel_name, session_name in live_panel_sessions
    )
    lines.extend(
        _session_tmux_config(session_name, resolved_theme, window_target=windows[session_name])
        for session_name in live_repo_sessions
    )
    lines.extend(_deck_tmux_config(session_name, resolved_theme) for session_name in live_decks)

    atomic_write_text(config_path, "\n".join(lines))
    # Always sourced: identical text can still mean a new session that
    # reuses a closed one's name and has never been themed.
    if live_panel_sessions or live_repo_sessions or live_decks:
        try:
            _run_tmux(["source-file", str(config_path)], check=True)
        except TmuxError:
            return config_path

    return config_path


def try_sync_panel_tmux_config() -> None:
    """:func:`sync_panel_tmux_config` where a failure costs only the theme."""
    try:
        sync_panel_tmux_config()
    except Exception:
        logger.debug("could not apply the tmux theme", exc_info=True)


__all__ = [
    "TmuxError",
    "attach_tmux_session",
    "capture_pane",
    "create_tmux_session",
    "kill_all_gd_sessions",
    "kill_tmux_session",
    "list_all_gd_sessions",
    "list_repo_sessions",
    "open_in_tmux",
    "prepare_attach",
    "send_key_to_session",
    "send_text_to_session",
    "sync_panel_tmux_config",
]
