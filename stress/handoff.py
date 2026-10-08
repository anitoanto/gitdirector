"""Record the console's handoff to a session and back, byte for byte.

Opens a session from the console's Sessions tab in a pty and checks the
terminal never sees the shell's screen: the console's hold (``?2026h``) comes
before Textual leaves the alt screen (``?1049l``), and the next ``?2026l`` is
tmux's, after its first redraw. Then detaches and checks the way back the
same way (DEV.md, "Decks"). Then opens the session with ``gitdirector cd``
from a pty, as a shell would, where tmux's own smcup/rmcup must do the work
and no hold may be left behind. Runs against a private tmux server and
gitdirector home, with sample repositories it makes itself.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import pty
import select
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
from pathlib import Path

HOLD = b"\x1b[?2026h"
RELEASE = b"\x1b[?2026l"
ENTER_ALT = b"\x1b[?1049h"
LEAVE_ALT = b"\x1b[?1049l"
PROJECT = Path(__file__).resolve().parents[1]


class Recorder:
    def __init__(self, fd: int) -> None:
        self.fd = fd
        self.out = bytearray()
        self.closed = False

    def wait(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end and not self.closed:
            ready, _, _ = select.select([self.fd], [], [], 0.05)
            if not ready:
                continue
            try:
                chunk = os.read(self.fd, 65536)
            except OSError:
                chunk = b""
            if not chunk:
                self.closed = True
                return
            self.out += chunk

    def wait_for(self, needle: bytes, timeout: float) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end and not self.closed:
            if needle in self.out:
                return True
            self.wait(0.1)
        return needle in self.out


def run(args: list[str], env: dict[str, str], cwd: Path | None = None) -> str:
    result = subprocess.run(args, env=env, cwd=cwd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        sys.exit(f"{' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}")
    return result.stdout


def make_repo(path: Path, env: dict[str, str]) -> None:
    path.mkdir(parents=True)
    (path / "README.md").write_text(f"# {path.name}\n")
    git = ["git", "-c", "user.name=gd", "-c", "user.email=gd@example.invalid"]
    run([*git, "init", "-q"], env, cwd=path)
    run([*git, "add", "."], env, cwd=path)
    run([*git, "commit", "-qm", "init"], env, cwd=path)


def check(name: str, ok: bool, failures: list[str]) -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    if not ok:
        failures.append(name)


def analyze_open(segment: bytes, failures: list[str]) -> None:
    print("open: console -> deck")
    hold = segment.find(HOLD)
    leave = segment.find(LEAVE_ALT)
    enter = segment.find(ENTER_ALT)
    tmux_hold = segment.find(HOLD, hold + 1) if hold >= 0 else -1
    release = segment.find(RELEASE)
    check(
        "the console holds the display before Textual leaves the alt screen",
        0 <= hold < leave,
        failures,
    )
    check(
        "the console re-enters the alt screen under the hold",
        leave < enter < release if enter >= 0 else False,
        failures,
    )
    check(
        "tmux brackets its first redraw (sync) under the hold",
        enter < tmux_hold < release,
        failures,
    )
    check("the hold ends only after tmux has drawn a frame", release - tmux_hold > 200, failures)


def analyze_return(segment: bytes, failures: list[str]) -> None:
    print("return: deck -> console")
    detached = segment.find(b"[detached (from session gd/deck/")
    hold = segment.rfind(HOLD, 0, detached) if detached >= 0 else -1
    enter = segment.find(ENTER_ALT, detached) if detached >= 0 else -1
    release = segment.find(RELEASE, detached) if detached >= 0 else -1
    check(
        "tmux's exit starts a hold (rmkx) before it clears and prints [detached]",
        0 <= hold < detached and RELEASE not in segment[hold:detached],
        failures,
    )
    check(
        "the terminal is never switched back to the shell's screen",
        detached >= 0 and LEAVE_ALT not in segment,
        failures,
    )
    check(
        "the console frame goes up under the hold, then the hold ends",
        detached < enter < release and release - enter > 200,
        failures,
    )


def analyze_cli(recording: bytes, failures: list[str]) -> None:
    print("shell: gitdirector cd -> deck -> shell")
    detached = recording.find(b"[detached")
    check(
        "tmux's own smcup takes the shell to the alt screen",
        0 <= recording.find(ENTER_ALT) < recording.find(b"\x1b[2J"),
        failures,
    )
    # tmux restores the screen (rmcup) just before it prints [detached].
    check(
        "tmux's own rmcup brings the shell's screen back on detach",
        detached >= 0 and recording.find(ENTER_ALT) < recording.rfind(LEAVE_ALT) < detached,
        failures,
    )
    check(
        "no hold is left on the terminal",
        recording.count(HOLD) == recording.count(RELEASE)
        and recording.rfind(HOLD) < recording.rfind(RELEASE),
        failures,
    )


def record_cli(session: str, env: dict[str, str], failures: list[str]) -> bytes:
    """Open *session* with `gitdirector cd` from a pty, as a shell would, and detach."""
    child, fd = pty.fork()
    if child == 0:
        os.chdir(PROJECT)
        os.execvpe("uv", ["uv", "run", "gitdirector", "cd", session], env)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
    rec = Recorder(fd)
    try:
        if not rec.wait_for(b"\x1b[2J", 60):
            sys.exit(
                f"gitdirector cd did not attach; the pty ended with: {bytes(rec.out[-800:])!r}"
            )
        rec.wait(4)
        for line in run(
            ["tmux", "list-clients", "-F", "#{client_tty} #{client_session}"], env
        ).splitlines():
            tty, _, client_session = line.partition(" ")
            if client_session.startswith("gd/deck/"):
                run(["tmux", "detach-client", "-t", tty], env)
        rec.wait(3)
    finally:
        try:
            os.kill(child, signal.SIGKILL)
            os.waitpid(child, 0)
        except (OSError, ChildProcessError):
            pass
    recording = bytes(rec.out)
    analyze_cli(recording, failures)
    return recording


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, help="save the raw pty recording here")
    opts = parser.parse_args()

    base = Path(tempfile.mkdtemp(prefix="gdhandoff.", dir="/tmp"))
    env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")}
    env.update(
        GITDIRECTOR_HOME=str(base / "home"),
        TMUX_TMPDIR=str(base / "tmux"),
        TERM="xterm-256color",
        LINES="40",
        COLUMNS="120",
    )
    (base / "tmux").mkdir()
    gd = ["uv", "run", "gitdirector"]
    failures: list[str] = []
    child = 0
    try:
        for name in ("alpha", "beta"):
            make_repo(base / "repos" / name, env)
            run([*gd, "link", str(base / "repos" / name)], env, cwd=PROJECT)
        session = run(
            [*gd, "gd-tmux", "alpha", "sleep 1000", "-d", "handoff sample"], env, cwd=PROJECT
        ).strip()
        print(f"sample session {session} on {env['TMUX_TMPDIR']}")

        child, fd = pty.fork()
        if child == 0:
            os.chdir(PROJECT)
            os.execvpe("uv", [*gd, "console"], env)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
        rec = Recorder(fd)
        if not rec.wait_for(ENTER_ALT, 60):
            tail = bytes(rec.out[-800:])
            sys.exit(f"the console did not come up; the pty ended with: {tail!r}")
        rec.wait(5)
        os.write(fd, b"2")
        rec.wait(2)
        opened = len(rec.out)
        os.write(fd, b"\r")
        rec.wait(6)
        # Only the console's own client: the sidebar pane holds a nested one
        # on a gd/view session, whose detach message would be drawn in the pane.
        clients = [
            line.split(" ", 1)[0]
            for line in run(
                ["tmux", "list-clients", "-F", "#{client_tty} #{client_session}"], env
            ).splitlines()
            if line.split(" ", 1)[-1].startswith("gd/deck/")
        ]
        if len(clients) != 1:
            sys.exit(f"expected one client on the deck after Enter, found {clients}")
        detached = len(rec.out)
        run(["tmux", "detach-client", "-t", clients[0]], env)
        rec.wait(4)
        quit_at = len(rec.out)
        os.write(fd, b"q")
        rec.wait(3)
        recording = bytes(rec.out)
        if opts.out:
            opts.out.write_bytes(recording)
        analyze_open(recording[opened:detached], failures)
        analyze_return(recording[detached:quit_at], failures)
        cli = record_cli(session, env, failures)
        if opts.out:
            opts.out.with_suffix(".cli.bin").write_bytes(cli)
    finally:
        if child > 0:
            try:
                os.kill(child, signal.SIGKILL)
                os.waitpid(child, 0)
            except (OSError, ChildProcessError):
                pass
        subprocess.run(["tmux", "kill-server"], env=env, capture_output=True)
        shutil.rmtree(base, ignore_errors=True)
    print("PASS" if not failures else f"FAIL: {len(failures)} check(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
