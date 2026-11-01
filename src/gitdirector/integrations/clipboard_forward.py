"""Hand a copy made in a GitDirector session to the terminal the user sees.

A deck or panel shows a session through a tmux client that runs in one of
its panes. tmux sends a copy's OSC 52 only to the clients showing the copied
pane, here that nested client, and drops the nested client's output to its
pane (``set-clipboard external``), so the copy never reaches the terminal.

GitDirector's copy bindings run this after every copy, inside its sessions
only. It climbs from the client that copied, through the pane its terminal
is, to the clients of that pane's session, until it reaches clients that are
terminals of their own, and has tmux send them the newest buffer (a client
that copied directly in a terminal already got it from tmux). Terminals
without OSC 52 (Terminal.app, VTE) ignore that, so the copy also goes to the
system clipboard through its native tool, when one is installed.

It runs as ``python -I -S``, uses the standard library only, prints nothing
and always exits 0.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

FORWARDED_OPTION = "@gd_clipboard_forwarded"
# A binding may copy nothing (the program in the pane took the click), and an
# older buffer is then not the user's latest copy.
_FRESH_SECS = 3
_MAX_HOPS = 4
_TIMEOUT_SECS = 3


def _tmux(socket: str, *args: str, data: bytes | None = None) -> bytes | None:
    result = subprocess.run(
        ["tmux", "-S", socket, *args],
        input=data,
        capture_output=True,
        timeout=_TIMEOUT_SECS,
        check=False,
    )
    return result.stdout if result.returncode == 0 else None


def _lines(output: bytes | None) -> list[list[str]]:
    if output is None:
        return []
    return [line.split("\t") for line in output.decode("utf-8", "replace").splitlines()]


def native_copy_command(
    platform: str = sys.platform, environ: dict[str, str] | os._Environ = os.environ
) -> list[str] | None:
    """The system clipboard's copy tool here, if one is installed."""
    candidates: list[list[str]] = []
    if platform == "darwin":
        candidates.append(["pbcopy"])
    elif platform.startswith("linux"):
        if environ.get("WSL_DISTRO_NAME"):
            candidates.append(["clip.exe"])
        if environ.get("WAYLAND_DISPLAY"):
            candidates.append(["wl-copy"])
        if environ.get("DISPLAY"):
            candidates += [["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]
    elif platform in ("win32", "cygwin", "msys"):
        candidates.append(["clip.exe"])
    return next((command for command in candidates if shutil.which(command[0])), None)


def _native_copy(data: bytes) -> None:
    command = native_copy_command()
    if command is None:
        return
    # xclip stays behind to serve the selection: its output must not be a
    # pipe this process waits on.
    subprocess.run(
        command,
        input=data,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=_TIMEOUT_SECS,
        check=False,
    )


def newest_buffer(rows: list[list[str]], now: float) -> tuple[str, str] | None:
    """``(name, mark)`` of the newest buffer, when it was made just now."""
    buffers = [(int(created), name) for created, name in rows if created.isdigit()]
    if not buffers:
        return None
    created, name = max(buffers, key=lambda buffer: buffer[0])
    if now - created > _FRESH_SECS:
        return None
    return name, f"{created} {name}"


def terminal_clients(tty: str, panes: list[list[str]], clients: list[list[str]]) -> list[str]:
    """Names of the terminal clients showing the nested client on *tty*.

    *panes* are ``(pane tty, session)`` rows (a grouped session lists the same
    pane again), *clients* ``(client tty, client name, session)`` rows.
    """
    sessions_by_tty: dict[str, set[str]] = {}
    for pane_tty, session in panes:
        sessions_by_tty.setdefault(pane_tty, set()).add(session)
    found: list[str] = []
    frontier, seen = {tty}, {tty}
    for hop in range(_MAX_HOPS + 1):
        upward: set[str] = set()
        for current in frontier:
            sessions = sessions_by_tty.get(current)
            if sessions is None:
                if hop:
                    found.extend(name for client_tty, name, _ in clients if client_tty == current)
                continue
            upward.update(client_tty for client_tty, _, session in clients if session in sessions)
        frontier = upward - seen
        seen |= frontier
        if not frontier:
            break
    return sorted(set(found))


def forward(socket: str, tty: str, now: float) -> None:
    buffer = newest_buffer(
        _lines(_tmux(socket, "list-buffers", "-F", "#{buffer_created}\t#{buffer_name}")), now
    )
    if buffer is None:
        return
    name, mark = buffer
    forwarded = _tmux(socket, "show-options", "-gqv", FORWARDED_OPTION)
    if forwarded is None or forwarded.decode().strip() == mark:
        return
    targets = terminal_clients(
        tty,
        _lines(_tmux(socket, "list-panes", "-a", "-F", "#{pane_tty}\t#{session_name}")),
        _lines(
            _tmux(socket, "list-clients", "-F", "#{client_tty}\t#{client_name}\t#{client_session}")
        ),
    )
    _tmux(socket, "set-option", "-g", FORWARDED_OPTION, mark)
    data = _tmux(socket, "save-buffer", "-b", name, "-")
    if not data:
        return
    for client in targets:
        _tmux(socket, "load-buffer", "-w", "-b", name, "-t", client, "-", data=data)
    _native_copy(data)


def main() -> None:
    try:
        socket, tty = sys.argv[1:3]
        if socket and tty:
            forward(socket, tty, time.time())
    except Exception:
        pass


if __name__ == "__main__":
    main()
