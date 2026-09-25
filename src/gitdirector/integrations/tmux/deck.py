"""The deck: an open session shown next to a sidebar of every session.

Opening a ``gd/<repo>/<purpose>/<N>`` session attaches the client to a deck,
``gd/deck/<id>``, rather than to the session itself. The deck's one window
has two panes:

- the sidebar (:data:`SIDEBAR_MODULE`), which lists the sessions and drives
  everything below;
- the main pane, a tmux client attached to a *view* of the shown session,
  exactly like a panel slot. Showing another session creates a view of it
  and switches that client over (``switch-client -c <pane tty>``), so
  nothing restarts and the real session is never touched.

A deck belongs to the client that opened it and is destroyed when that
client leaves (``destroy-unattached``); each view goes with the client in
the main pane.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import shlex
import shutil
import sys
import time
from dataclasses import dataclass

from ...launch_context import neutral_directory
from ...ui_theme import resolve_panel_theme, textual_surface
from .core import (
    _FIRST_WINDOW,
    TmuxError,
    _chain_tmux_commands,
    _is_deck_session,
    _panel_session_label,
    _parse_gd_session_name,
    _resolved_panel_theme_name,
    _run_tmux,
    _scrub_session_environment,
    _session_exists,
    _session_option_target,
    _tmux_child_environment_command,
    _tmux_new_session_environment_args,
    attach_client_env,
    guard_session_window,
    kill_tmux_session,
    respawn_pane,
    sync_panel_tmux_config,
    view_attach_command,
)

logger = logging.getLogger(__name__)

DECK_PREFIX = "gd/deck/"
_DECK_VIEW_PREFIX = "gd/view/deck-"
SIDEBAR_MODULE = "gitdirector.commands.tui.sidebar"

# Session options of a deck.
DECK_MAIN_OPTION = "@gd_deck_main"
DECK_SIDEBAR_OPTION = "@gd_deck_sidebar"
DECK_TARGET_OPTION = "@gd_deck_target"
DECK_RESPAWN_SIDEBAR_OPTION = "@gd_deck_respawn_sidebar"
# Where a client that switched into the deck from inside tmux goes back to.
DECK_RETURN_OPTION = "@gd_deck_return"
# Pane option: the main pane shows a message, not a session.
DECK_PLACEHOLDER_OPTION = "@gd_deck_placeholder"
# Global: the sidebar is collapsed to a rail, in every deck.
SIDEBAR_COLLAPSED_OPTION = "@gd_sidebar_collapsed"

SIDEBAR_WIDTH = 32
SIDEBAR_RAIL_WIDTH = 5
# Narrower windows give the sidebar a third of their width.
_NARROW_WINDOW = 96
# An unattached deck older than this was left by a client that never
# arrived (a crash between creating and attaching).
_STALE_DECK_SECS = 60

# The main pane's fallback once its client exits: the sidebar notices the
# command and replaces the pane with a message.
ATTACH_ENDED_COMMAND = "sleep"
_WAIT_FOREVER = f"exec {ATTACH_ENDED_COMMAND} 2147483647"

_SIDEBAR = f"#{{{DECK_SIDEBAR_OPTION}}}"
_MAIN = f"#{{{DECK_MAIN_OPTION}}}"
_SIDEBAR_EXISTS = f"#{{P:#{{?#{{==:#{{pane_id}},{_SIDEBAR}}},1,}}}}"
_SIDEBAR_WIDTH_FORMAT = (
    f"#{{?#{{==:#{{{SIDEBAR_COLLAPSED_OPTION}}},1}},{SIDEBAR_RAIL_WIDTH},"
    f"#{{?#{{e|<:#{{window_width}},{_NARROW_WINDOW}}},#{{e|/:#{{window_width}},3}},"
    f"{SIDEBAR_WIDTH}}}}}"
)
_RESIZE_SIDEBAR = f"resize-pane -t {_SIDEBAR} -x {_SIDEBAR_WIDTH_FORMAT}"
_RESIZE_SIDEBAR_HOOK = f'if-shell -F "{_SIDEBAR_EXISTS}" "run-shell -C \'{_RESIZE_SIDEBAR}\'"'

_IN_DECK = f"#{{m:{DECK_PREFIX}*,#{{session_name}}}}"
_DECK_BINDING_MARKER = f"m:{DECK_PREFIX}*"
_SIDEBAR_CLOSED_MESSAGE = "display-message 'sidebar closed: prefix Tab brings it back'"
_LIST_KEYS_LINE = re.compile(
    r"^bind-key\s+(?P<repeat>-r\s+)?-T\s+(?P<table>\S+)\s+(?P<key>\S+)\s+(?P<command>.*)$"
)
# Any GitDirector session: a deck's shown session is a gd/view/ one.
_IN_GITDIRECTOR = "#{m:gd/*,#{session_name}}"
_GITDIRECTOR_BINDING_MARKER = "m:gd/*"


def sidebar_width(window_width: int, collapsed: bool) -> int:
    """Python twin of the width format the resize hook applies."""
    if collapsed:
        return SIDEBAR_RAIL_WIDTH
    if window_width < _NARROW_WINDOW:
        return window_width // 3
    return SIDEBAR_WIDTH


def _new_deck_name() -> str:
    return f"{DECK_PREFIX}{os.getpid()}-{secrets.token_hex(3)}"


def _new_view_name(deck: str) -> str:
    return f"{_DECK_VIEW_PREFIX}{deck.rsplit('/', 1)[-1]}-{secrets.token_hex(3)}"


def _deck_target(deck: str) -> str:
    return _session_option_target(deck)


def _tmux_stdout(args: list[str]) -> str:
    return _run_tmux(args, check=True, text=True).stdout.strip()


def _socket_path() -> str:
    return _tmux_stdout(["display-message", "-p", "#{socket_path}"])


def _global_option(name: str) -> str:
    result = _run_tmux(["show-options", "-gqv", name], text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def sidebar_collapsed() -> bool:
    return _global_option(SIDEBAR_COLLAPSED_OPTION) == "1"


def prefix_key_label() -> str:
    return _global_option("prefix") or "C-b"


def _view_command(socket: str, session_name: str, view: str) -> str:
    """The main pane's command: a client on a new view of *session_name*.

    ``-S`` pins the client to this server whatever the pane's environment
    says; ``env -u TMUX`` lets it attach from inside a pane at all.
    """
    tmux = f"tmux -S {shlex.quote(socket)}"
    attach = view_attach_command(tmux, session_name, view, "mouse on", "detach-on-destroy on")
    script = f"{attach}; clear; {_WAIT_FOREVER}"
    return _tmux_child_environment_command(f"env -u TMUX sh -c {shlex.quote(script)}")


def _sidebar_command(deck: str) -> str:
    return _tmux_child_environment_command(shlex.join([sys.executable, "-m", SIDEBAR_MODULE, deck]))


def _rgb(hex_color: str) -> str:
    value = hex_color.lstrip("#")
    red, green, blue = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    return f"\033[38;2;{red};{green};{blue}m"


def _placeholder_command(title: str, detail: str, hint: str) -> str:
    """A centred message that redraws itself when the pane is resized."""
    theme = resolve_panel_theme(_resolved_panel_theme_name())
    bold, reset = "\033[1m", "\033[0m"
    muted = _rgb(theme.border_inactive)
    lines = [
        (f"{bold}{_rgb(theme.foreground)}{title}{reset}", title),
        (f"{_rgb(theme.accent)}{detail}{reset}", detail),
        ("", ""),
        (f"{muted}{hint}{reset}", hint),
    ]
    draw = [
        "set -- $(stty size 2>/dev/null)",
        "rows=${1:-24}; cols=${2:-80}",
        "printf '\\033[2J\\033[H'",
        f"i=0; top=$(( (rows - {len(lines)}) / 2 ))",
        "while [ $i -lt $top ]; do echo; i=$((i + 1)); done",
    ]
    for styled, plain in lines:
        draw.append(
            f"pad=$(( (cols - {len(plain)}) / 2 )); [ $pad -lt 0 ] && pad=0; "
            f"printf \"%${{pad}}s%s\\n\" '' {shlex.quote(styled)}"
        )
    script = "; ".join(
        [
            "stty -echo -icanon 2>/dev/null",
            "printf '\\033[?25l'",
            "draw() { " + "; ".join(draw) + "; }",
            "trap draw WINCH",
            "draw",
            "while :; do sleep 86400 & p=$!; wait $p; kill $p 2>/dev/null; done",
        ]
    )
    return _tmux_child_environment_command(f"sh -c {shlex.quote(script)}")


@dataclass(frozen=True)
class DeckPane:
    pane_id: str
    tty: str
    command: str
    placeholder: bool
    active: bool


@dataclass(frozen=True)
class DeckState:
    target: str | None
    main: DeckPane | None
    sidebar: DeckPane | None
    #: A client is attached in the main pane (a view is showing).
    main_attached: bool
    #: The deck has a client and its sidebar pane has focus.
    sidebar_focused: bool
    live_sessions: frozenset[str]


_STATE_SEPARATOR = "\t"


def read_deck_state(deck: str) -> DeckState | None:
    """Everything the sidebar reconciles against, in one tmux call; None once the deck is gone."""
    target = _deck_target(deck)
    sep = _STATE_SEPARATOR
    result = _run_tmux(
        _chain_tmux_commands(
            [
                [
                    "display-message",
                    "-p",
                    "-t",
                    target,
                    sep.join(
                        [
                            "D",
                            f"#{{{DECK_TARGET_OPTION}}}",
                            _MAIN,
                            _SIDEBAR,
                            "#{session_attached}",
                        ]
                    ),
                ],
                [
                    "list-panes",
                    "-t",
                    f"={deck}:{_FIRST_WINDOW}",
                    "-F",
                    sep.join(
                        [
                            "P",
                            "#{pane_id}",
                            "#{pane_tty}",
                            "#{pane_current_command}",
                            f"#{{{DECK_PLACEHOLDER_OPTION}}}",
                            "#{pane_active}",
                        ]
                    ),
                ],
                ["list-clients", "-F", sep.join(["C", "#{client_tty}"])],
                ["list-sessions", "-F", sep.join(["S", "#{session_name}"])],
            ]
        ),
        text=True,
    )
    if result.returncode != 0:
        return None
    header: list[str] | None = None
    panes: dict[str, DeckPane] = {}
    client_ttys: set[str] = set()
    sessions: set[str] = set()
    for line in result.stdout.splitlines():
        kind, _, rest = line.partition(sep)
        fields = rest.split(sep)
        if kind == "D" and len(fields) == 4:
            header = fields
        elif kind == "P" and len(fields) == 5:
            pane_id, tty, command, placeholder, active = fields
            panes[pane_id] = DeckPane(pane_id, tty, command, placeholder == "1", active == "1")
        elif kind == "C" and fields[0]:
            client_ttys.add(fields[0])
        elif kind == "S" and fields[0]:
            sessions.add(fields[0])
    if header is None:
        return None
    target_name, main_id, sidebar_id, attached = header
    main = panes.get(main_id)
    sidebar = panes.get(sidebar_id)
    return DeckState(
        target=target_name or None,
        main=main,
        sidebar=sidebar,
        main_attached=main is not None and main.tty in client_ttys,
        sidebar_focused=sidebar is not None and sidebar.active and attached not in ("", "0"),
        live_sessions=frozenset(sessions),
    )


def _client_size() -> tuple[int, int]:
    """The size of the terminal the client will attach from."""
    for stream in (sys.__stdin__, sys.__stdout__, sys.__stderr__):
        try:
            size = os.get_terminal_size(stream.fileno())
        except (AttributeError, OSError, ValueError):
            continue
        if size.columns and size.lines:
            return size.columns, size.lines
    size = shutil.get_terminal_size()
    return size.columns, size.lines


def create_deck(session_name: str, *, return_to: str | None = None) -> str:
    """Build a deck showing *session_name*, detached; the caller attaches to it.

    Everything is laid out at the exact size the client will see (less its
    status line), so attaching resizes nothing: the shown session is fitted
    to its pane once, before the deck is on screen.
    """
    reap_stale_decks()
    deck = _new_deck_name()
    target = _deck_target(deck)
    cols, lines = _client_size()
    rows = max(lines - 1, 1)
    width = sidebar_width(cols, sidebar_collapsed())
    # The sidebar pane comes first so the main pane's client attaches once,
    # already at its final width.
    sidebar = _tmux_stdout(
        [
            "new-session",
            "-d",
            *_tmux_new_session_environment_args(),
            "-s",
            deck,
            "-n",
            "gitdirector",
            "-x",
            str(cols),
            "-y",
            str(rows),
            "-c",
            neutral_directory(),
            "-P",
            "-F",
            "#{pane_id}",
            _sidebar_command(deck),
        ]
    )
    try:
        _scrub_session_environment(deck)
        # A view is about to be grouped with it: its window must not close itself.
        guard_session_window(session_name)
        respawn_sidebar = shlex.join(["split-window", "-h", "-b", "-f", "-l", str(width)]) + (
            f" {shlex.quote(_sidebar_command(deck))}"
        )
        main = _tmux_stdout(
            [
                "split-window",
                "-h",
                "-l",
                str(max(cols - width - 1, 1)),
                "-t",
                sidebar,
                "-P",
                "-F",
                "#{pane_id}",
                _view_command(_socket_path(), session_name, _new_view_name(deck)),
            ]
        )
        window = f"={deck}:{_FIRST_WINDOW}"
        surface = textual_surface(_resolved_panel_theme_name())
        # Painted before the sidebar app has started, in the colour it paints.
        sidebar_style = (
            [["set-option", "-p", "-t", sidebar, "window-style", f"bg={surface}"]]
            if surface
            else []
        )
        _run_tmux(
            _chain_tmux_commands(
                [
                    ["set-option", "-t", target, "status", "on"],
                    *sidebar_style,
                    ["set-option", "-t", target, DECK_MAIN_OPTION, main],
                    ["set-option", "-t", target, DECK_SIDEBAR_OPTION, sidebar],
                    ["set-option", "-t", target, DECK_TARGET_OPTION, session_name],
                    ["set-option", "-t", target, DECK_RESPAWN_SIDEBAR_OPTION, respawn_sidebar],
                    ["set-option", "-t", target, "detach-on-destroy", "on"],
                    ["set-window-option", "-t", window, "remain-on-exit", "off"],
                    ["set-hook", "-w", "-t", window, "window-resized", _RESIZE_SIDEBAR_HOOK],
                    ["select-pane", "-t", main],
                ]
            ),
            check=True,
        )
        if return_to:
            _run_tmux(["set-option", "-t", target, DECK_RETURN_OPTION, return_to], check=True)
        sync_panel_tmux_config()
        ensure_deck_bindings()
    except BaseException:
        kill_tmux_session(deck)
        raise
    return deck


def show_session(deck: str, session_name: str, *, focus: bool = True) -> None:
    """Show *session_name* in the deck's main pane.

    A client already in the pane is switched over to a new view; otherwise
    the pane is respawned with one. The old view is unattached either way,
    and tmux destroys it. A pane whose respawn fails is killed, and the
    sidebar puts a new one in its place.
    """
    state = read_deck_state(deck)
    if state is None:
        raise TmuxError(f"deck no longer exists: {deck}")
    if state.main is None:
        raise TmuxError(f"deck has no main pane: {deck}")
    view = _new_view_name(deck)
    # A view is about to be grouped with it: its window must not close itself.
    guard_session_window(session_name)
    if state.main_attached:
        view_target = _session_option_target(view)
        commands = [
            ["new-session", "-d", "-t", f"={session_name}", "-s", view],
            ["set-option", "-t", view_target, "status", "off"],
            ["set-option", "-t", view_target, "mouse", "on"],
            ["set-option", "-t", view_target, "detach-on-destroy", "on"],
            ["switch-client", "-c", state.main.tty, "-t", f"={view}"],
            ["set-option", "-t", view_target, "destroy-unattached", "on"],
        ]
    else:
        respawn_pane(state.main.pane_id, _view_command(_socket_path(), session_name, view))
        commands = []
    commands.extend(
        [
            ["set-option", "-p", "-u", "-t", state.main.pane_id, DECK_PLACEHOLDER_OPTION],
            ["set-option", "-t", _deck_target(deck), DECK_TARGET_OPTION, session_name],
        ]
    )
    if focus:
        commands.append(["select-pane", "-t", state.main.pane_id])
    _run_tmux(_chain_tmux_commands(commands), check=True)


def show_placeholder(
    deck: str, main_pane: str, title: str, detail: str, hint: str, *, focus_sidebar: bool = True
) -> None:
    """Replace whatever the main pane shows with a message."""
    target = _deck_target(deck)
    respawn_pane(main_pane, _placeholder_command(title, detail, hint))
    commands = [
        ["set-option", "-p", "-t", main_pane, DECK_PLACEHOLDER_OPTION, "1"],
        ["set-option", "-u", "-t", target, DECK_TARGET_OPTION],
    ]
    if focus_sidebar:
        commands.append(["run-shell", "-t", target, "-C", f"select-pane -t {_SIDEBAR}"])
    _run_tmux(_chain_tmux_commands(commands), check=True)


def recreate_main_pane(deck: str, sidebar_pane: str) -> str:
    """Put a main pane back beside the sidebar after it was closed."""
    main = _tmux_stdout(
        [
            "split-window",
            "-h",
            "-f",
            "-d",
            "-t",
            sidebar_pane,
            "-P",
            "-F",
            "#{pane_id}",
            _tmux_child_environment_command(f"sh -c {shlex.quote(_WAIT_FOREVER)}"),
        ]
    )
    _run_tmux(
        _chain_tmux_commands(
            [
                ["set-option", "-t", _deck_target(deck), DECK_MAIN_OPTION, main],
                ["run-shell", "-t", sidebar_pane, "-C", _RESIZE_SIDEBAR],
            ]
        ),
        check=True,
    )
    return main


def register_sidebar(deck: str, pane_id: str) -> None:
    """Record *pane_id* as the deck's sidebar and give it its width."""
    _run_tmux(
        _chain_tmux_commands(
            [
                ["set-option", "-t", _deck_target(deck), DECK_SIDEBAR_OPTION, pane_id],
                ["run-shell", "-t", pane_id, "-C", _RESIZE_SIDEBAR],
            ]
        ),
        check=True,
    )


def set_sidebar_collapsed(deck: str, collapsed: bool) -> None:
    _run_tmux(
        _chain_tmux_commands(
            [
                ["set-option", "-g", SIDEBAR_COLLAPSED_OPTION, "1" if collapsed else "0"],
                ["run-shell", "-t", _deck_target(deck), "-C", _RESIZE_SIDEBAR],
            ]
        ),
        check=True,
    )


def select_pane(pane_id: str) -> None:
    _run_tmux(["select-pane", "-t", pane_id])


def close_deck(deck: str) -> None:
    """Leave the deck: its client goes back to wherever it came from.

    A client that switched in from inside tmux is switched back to its
    session first; ``detach-on-destroy`` has no value meaning "the session
    this client came from". Any other client detaches with the deck.
    """
    target = _deck_target(deck)
    result = _run_tmux(
        _chain_tmux_commands(
            [
                ["display-message", "-p", "-t", target, f"#{{{DECK_RETURN_OPTION}}}"],
                ["list-clients", "-t", target, "-F", "#{client_tty}"],
            ]
        ),
        text=True,
    )
    if result.returncode == 0:
        return_to, *clients = result.stdout.splitlines()
        if return_to and _session_exists(return_to):
            switches = [["switch-client", "-c", tty, "-t", f"={return_to}"] for tty in clients]
            if switches:
                _run_tmux(_chain_tmux_commands(switches))
    kill_tmux_session(deck)


def reap_stale_decks() -> list[str]:
    """Kill decks, and deck views, that no client is attached to any more."""
    result = _run_tmux(
        [
            "list-sessions",
            "-F",
            "#{session_name}\t#{session_attached}\t#{session_created}",
        ],
        text=True,
    )
    if result.returncode != 0:
        return []
    now = time.time()
    reaped: list[str] = []
    for line in result.stdout.splitlines():
        name, _, rest = line.partition("\t")
        attached, _, created = rest.partition("\t")
        if not (_is_deck_session(name) or name.startswith(_DECK_VIEW_PREFIX)):
            continue
        if attached not in ("", "0"):
            continue
        try:
            age = now - int(created)
        except ValueError:
            continue
        if age > _STALE_DECK_SECS and kill_tmux_session(name):
            reaped.append(name)
    return reaped


def _deck_key_commands() -> dict[str, str]:
    """What each wrapped prefix key does inside a deck."""

    def to_sidebar(key: str) -> str:
        return (
            f'run-shell -C "#{{?{_SIDEBAR_EXISTS},send-keys -t {_SIDEBAR} {key},'
            f'{_SIDEBAR_CLOSED_MESSAGE}}}"'
        )

    toggle_focus = (
        f"#{{?#{{==:#{{pane_id}},{_SIDEBAR}}},select-pane -t {_MAIN},select-pane -t {_SIDEBAR}}}"
    )
    return {
        # Unbound by default in tmux.
        "Tab": (
            f'run-shell -C "#{{?{_SIDEBAR_EXISTS},{toggle_focus},'
            f'#{{{DECK_RESPAWN_SIDEBAR_OPTION}}}}}"'
        ),
        "b": to_sidebar("b"),
    }


_ORIGINAL_BINDING_OPTIONS = {
    "Tab": "@gd_prefix_original_tab",
    "b": "@gd_prefix_original_b",
}
# tmux scrolls copy mode 5 lines per wheel notch; one reads like the console.
_WHEEL_COMMANDS = {
    "WheelUpPane": "select-pane ; send-keys -X scroll-up",
    "WheelDownPane": "select-pane ; send-keys -X scroll-down",
}
_WHEEL_TABLES = {"copy-mode": "copy_mode", "copy-mode-vi": "copy_mode_vi"}


def _key_bindings() -> dict[tuple[str, str], tuple[bool, str]]:
    result = _run_tmux(["list-keys"], text=True)
    bindings: dict[tuple[str, str], tuple[bool, str]] = {}
    if result.returncode != 0:
        return bindings
    for line in result.stdout.splitlines():
        match = _LIST_KEYS_LINE.match(line.strip())
        if match is None:
            continue
        key = match["key"]
        if len(key) == 2 and key.startswith("\\"):
            key = key[1:]
        bindings[(match["table"], key)] = (match["repeat"] is not None, match["command"].strip())
    return bindings


def _as_command_string(listed: str) -> str:
    # list-keys separates commands with "\;", which a command string reads as
    # a literal ";" argument.
    return re.sub(r"(?<=\s)\\;(?=\s|$)", ";", listed)


def _wrap_binding(
    bindings: dict[tuple[str, str], tuple[bool, str]],
    table: str,
    key: str,
    option: str,
    condition: str,
    marker: str,
    command: str,
) -> list[list[str]]:
    repeat, current = bindings.get((table, key), (False, ""))
    commands: list[list[str]] = []
    if marker in current:
        original = _global_option(option)
    else:
        original = current
        if original:
            commands.append(["set-option", "-g", option, original])
        else:
            commands.append(["set-option", "-gu", option])
    bind = ["bind-key", *(["-r"] if repeat else []), "-T", table, key]
    bind.extend(["if-shell", "-F", condition, command])
    if original:
        bind.append(_as_command_string(original))
    commands.append(bind)
    return commands


def ensure_deck_bindings() -> None:
    """Give GitDirector's keys their meaning, inside its sessions only.

    ``prefix Tab`` and ``prefix b`` work the deck, and the mouse wheel scrolls
    copy mode a line at a time. Each key is rebound to ``if-shell -F
    <condition> <our command> <original>``: elsewhere the user's own binding
    (or tmux's default) still runs. The original is kept in a global option so
    re-wrapping is idempotent, and a binding the user changed since is picked
    up again.
    """
    bindings = _key_bindings()
    commands: list[list[str]] = []
    for key, deck_command in _deck_key_commands().items():
        commands.extend(
            _wrap_binding(
                bindings,
                "prefix",
                key,
                _ORIGINAL_BINDING_OPTIONS[key],
                _IN_DECK,
                _DECK_BINDING_MARKER,
                deck_command,
            )
        )
    for table, slug in _WHEEL_TABLES.items():
        for key, wheel_command in _WHEEL_COMMANDS.items():
            commands.extend(
                _wrap_binding(
                    bindings,
                    table,
                    key,
                    f"@gd_original_{slug}_{key.lower()}",
                    _IN_GITDIRECTOR,
                    _GITDIRECTOR_BINDING_MARKER,
                    wheel_command,
                )
            )
    _run_tmux(_chain_tmux_commands(commands), check=True)


def _current_session() -> str | None:
    result = _run_tmux(["display-message", "-p", "#{client_session}"], text=True)
    name = result.stdout.strip() if result.returncode == 0 else ""
    return name or None


def open_deck(session_name: str) -> str:
    """Build the deck *session_name* opens in, for this client."""
    return_to = _current_session() if os.environ.get("TMUX") else None
    return create_deck(session_name, return_to=return_to)


def attach_deck(session_name: str, deck: str | None = None) -> bool:
    """Attach to *deck* (a new one for *session_name* when None); see :func:`.core.attach_tmux_session`."""
    inside_tmux = bool(os.environ.get("TMUX"))
    deck = deck or open_deck(session_name)
    target = _deck_target(deck)
    # Set only once a client is on it: tmux may otherwise destroy the
    # deck before anyone attaches.
    destroy_when_left = ["set-option", "-t", target, "destroy-unattached", "on"]
    if inside_tmux:
        try:
            _run_tmux(
                _chain_tmux_commands([["switch-client", "-t", f"={deck}"], destroy_when_left]),
                check=True,
                capture_output=False,
            )
        except BaseException:
            kill_tmux_session(deck)
            raise
        return False
    # Blocks for the whole visit; see attach_tmux_session for why there is
    # no timeout and why a non-zero exit is not necessarily a failure.
    result = _run_tmux(
        _chain_tmux_commands([["attach-session", "-t", f"={deck}"], destroy_when_left]),
        capture_output=False,
        timeout=None,
        extra_env=attach_client_env(),
    )
    deck_left_behind = kill_tmux_session(deck)
    if result.returncode != 0 and deck_left_behind:
        raise TmuxError(
            "tmux attach-session failed",
            args_list=list(result.args),
            returncode=result.returncode,
        )
    return True


def session_label(session_name: str) -> str:
    return _panel_session_label(session_name) or session_name


def is_repo_session(session_name: str) -> bool:
    return _parse_gd_session_name(session_name) is not None
