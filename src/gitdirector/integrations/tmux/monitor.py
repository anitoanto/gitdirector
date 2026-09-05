"""Session status monitoring for ``gd/*`` tmux sessions.

Status is derived only from signals every terminal program exposes, so the
same rules apply to a shell, a build, a dev server, or any AI agent:

* the process tree under the pane (is a shell the foreground process?)
* whether the visible pane content changed recently
* whether the process tree consumed CPU recently
* whether the pane's tty is in raw mode (an interactive program reading keys)
* the terminal bell

See :func:`resolve_pane_status` for how they combine.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shlex
import subprocess
import tempfile
import termios
import threading
import time
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from itertools import zip_longest
from pathlib import Path

from ...agents import (
    AGENT_INTERRUPTS_OPTION,
    AGENT_INTERRUPTS_UNREPORTED,
    AGENT_STATE_OPTION,
    AGENT_STATES,
)
from .core import (
    GD_DESCRIPTION_OPTION,
    GD_REPO_LABEL_OPTION,
    TMUX_COMMAND_TIMEOUT,
    TmuxError,
    _active_pane_target,
    _parse_gd_session_name,
    _run_tmux,
    _tmux_child_environment_command,
    _tmux_server_is_gone,
    kill_tmux_session,
    session_entry,
)

logger = logging.getLogger(__name__)


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


def launch_command_in_tmux_session(session_name: str, command: str) -> Path:
    """Run *command* in *session_name* and self-destruct the session on exit.

    The command is started by respawning the active pane, avoiding races with
    the fresh session's interactive shell startup. When the command exits, the
    wrapping shell detaches any attached client and kills the session so the
    lifecycle matches a one-shot invocation.

    A temporary marker path is returned; it is created the moment the command
    starts, so callers that need to wait on a startup signal (such as the
    TUI's agent loading screen) can poll for it. The marker is left for the
    caller to consume, which avoids fast-exit races.
    """
    ready_marker = _make_agent_ready_marker()
    ready_marker_quoted = shlex.quote(str(ready_marker))
    pane_target = _active_pane_target(session_name)
    quoted_session_target = shlex.quote(f"={session_name}")
    command_script = f"sh -lc {shlex.quote(command)}"
    cleanup_script = (
        "clear; "
        f"touch {ready_marker_quoted} >/dev/null 2>&1 || true; "
        f"{command_script}; "
        "status=$?; "
        f"tmux detach-client -s {quoted_session_target} >/dev/null 2>&1 || true; "
        f"tmux kill-session -t {quoted_session_target} >/dev/null 2>&1 || true; "
        f"rm -f {ready_marker_quoted} >/dev/null 2>&1 || true; "
        "exit $status"
    )
    result = _run_tmux(
        [
            "respawn-pane",
            "-k",
            "-t",
            pane_target,
            _tmux_child_environment_command(f"sh -lc {shlex.quote(cleanup_script)}"),
        ],
    )
    if isinstance(result.returncode, int) and result.returncode != 0:
        kill_tmux_session(session_name)
        ready_marker.unlink(missing_ok=True)
        raise TmuxError(
            "tmux respawn-pane failed",
            args_list=list(result.args) if isinstance(result.args, list) else None,
            returncode=result.returncode,
            stderr=result.stderr,
        )
    return ready_marker


_SHELL_COMMANDS = frozenset({"zsh", "bash", "fish", "sh", "dash", "tcsh", "csh", "ksh"})

STATUS_WAITING = "waiting"
STATUS_RUNNING = "running"
STATUS_IDLE = "idle"

# How often the monitor samples tmux.
_POLL_SECS = 1.0
# A non-shell program whose visible output and CPU use have both been quiet
# for this long is no longer working.
_SILENCE_THRESHOLD_SECS = 4.0
# A shell prompt counts as busy for this long after its last visible change,
# which covers the output of a command that just finished.
_SHELL_ACTIVITY_GRACE_SECS = 2.0
# Output that arrives together with a bell (the final render of a result)
# must not immediately cancel the bell.
_BELL_GRACE_SECS = 1.0
# CPU the process tree must burn over a short window to count as active.
# Idle programs still do periodic housekeeping (an agent at its prompt was
# measured at 70 ms in a single second), so a lone burst must not count; real
# work -- a build, a test run, rendering a stream -- sustains far more.
_CPU_WINDOW_SECS = 3.0
_CPU_ACTIVE_MIN_SECS = 0.5
# A change confined to this many cells that flips straight back is a
# program drawing its own blinking cursor, not work.
_NOISE_MAX_CELLS = 2

# Only for agents whose hooks stay silent on a user interrupt: a reported
# "running" that has shown no visible change and no CPU for this long was
# interrupted, since a working agent redraws its spinner every second. A
# reported "waiting" is only stale once the screen changed after the report
# (the prompt is gone) and then went quiet.
_AGENT_REPORT_STALE_SECS = 5.0
# Output rendered together with a report (the prompt itself) belongs to it.
_AGENT_REPORT_RENDER_SECS = 1.0

_CONTROL_MODE_STOP_WAIT_SECS = 5.0
_CONTROL_MODE_KILL_WAIT_SECS = 2.0
_CONTROL_MODE_FAILURE_BACKOFF_SECS = 30.0

_PANE_LIST_SEPARATOR = "\t"
_PANE_LIST_FIELDS = (
    "#{session_name}",
    "#{pane_current_command}",
    "#{pane_dead}",
    "#{pane_pid}",
    "#{window_bell_flag}",
    "#{pane_active}",
    "#{pane_tty}",
    "#{window_activity}",
    "#{mouse_any_flag}",
    "#{alternate_on}",
    f"#{{{AGENT_STATE_OPTION}}}",
    f"#{{{AGENT_INTERRUPTS_OPTION}}}",
    f"#{{{GD_REPO_LABEL_OPTION}}}",
    f"#{{{GD_DESCRIPTION_OPTION}}}",
)
_PANE_LIST_FORMAT = _PANE_LIST_SEPARATOR.join(_PANE_LIST_FIELDS)


@dataclass(frozen=True)
class PaneSample:
    """One session's active pane as reported by a single ``list-panes`` call."""

    session_name: str
    command: str
    dead: bool
    pane_pid: int
    bell: bool
    tty: str = ""
    #: Epoch seconds of the last output tmux saw in the window (0 if unknown).
    activity: int = 0
    #: Mouse tracking or the alternate screen is on: a full-screen program.
    interactive_hint: bool = False
    #: Status the agent reported itself through its hooks ("" when none).
    agent_state: str = ""
    #: The agent's hooks stay silent on a user interrupt.
    agent_interrupts_unreported: bool = False
    #: User-facing metadata stored on the session, for the Sessions tab.
    repo_label: str = ""
    description: str = ""


@dataclass(frozen=True)
class ProcessSnapshot:
    children_by_parent: dict[int, list[int]]
    commands_by_pid: dict[int, str]
    pgid_by_pid: dict[int, int]
    tpgid_by_pid: dict[int, int]
    cpu_seconds_by_pid: dict[int, float]

    @classmethod
    def empty(cls) -> ProcessSnapshot:
        return cls({}, {}, {}, {}, {})


def _normalize_process_command(raw_args: str) -> str:
    token = raw_args.strip().split(" ", 1)[0]
    if not token:
        return ""
    return Path(token).name


def _parse_cpu_seconds(text: str) -> float:
    """Parse ``ps`` ``time`` output: ``[[dd-]hh:]mm:ss[.cc]``."""
    days = 0
    if "-" in text:
        day_text, _, text = text.partition("-")
        try:
            days = int(day_text)
        except ValueError:
            return 0.0
    total = 0.0
    try:
        for part in text.split(":"):
            total = total * 60 + float(part)
    except ValueError:
        return 0.0
    return days * 86400 + total


_PS_ROW_RE = re.compile(r"\s*(\d+)\s+(\d+)\s+(-?\d+)\s+(-?\d+)\s+(\S+)\s+(.*)")


def _get_process_snapshot() -> ProcessSnapshot:
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,pgid=,tpgid=,time=,args="],
            capture_output=True,
            text=True,
            check=False,
            timeout=TMUX_COMMAND_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return ProcessSnapshot.empty()
    if result.returncode != 0:
        return ProcessSnapshot.empty()

    snapshot = ProcessSnapshot.empty()
    for line in result.stdout.splitlines():
        match = _PS_ROW_RE.match(line)
        if match is None:
            continue
        pid = int(match.group(1))
        ppid = int(match.group(2))
        snapshot.pgid_by_pid[pid] = int(match.group(3))
        snapshot.tpgid_by_pid[pid] = int(match.group(4))
        snapshot.cpu_seconds_by_pid[pid] = _parse_cpu_seconds(match.group(5))
        snapshot.commands_by_pid[pid] = _normalize_process_command(match.group(6))
        snapshot.children_by_parent.setdefault(ppid, []).append(pid)
    return snapshot


def _descendants(pane_pid: int, snapshot: ProcessSnapshot) -> list[tuple[int, int, str]]:
    """``(depth, pid, command)`` for every process under *pane_pid*."""
    found: list[tuple[int, int, str]] = []
    stack: list[tuple[int, int]] = [(pane_pid, 0)]
    seen: set[int] = set()
    while stack:
        pid, depth = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        for child in snapshot.children_by_parent.get(pid, []):
            stack.append((child, depth + 1))
            command = snapshot.commands_by_pid.get(child, "")
            if command:
                found.append((depth + 1, child, command))
    return found


def _is_shell(command: str) -> bool:
    return command.lstrip("-") in _SHELL_COMMANDS


def _resolve_pane_command(pane_pid: int, fallback_command: str, snapshot: ProcessSnapshot) -> str:
    """Name the program the user would consider "running" in the pane.

    The pane's own process is a shell; what matters is the job it is
    running. Prefer the shallowest non-shell process in the terminal's
    foreground process group, then any non-shell descendant, then the
    deepest shell (a shell script).
    """
    descendants = _descendants(pane_pid, snapshot)
    if not descendants:
        return fallback_command

    non_shell = [entry for entry in descendants if not _is_shell(entry[2])]
    if not non_shell:
        return max(descendants, key=lambda entry: (entry[0], entry[1]))[2]

    pane_tpgid = snapshot.tpgid_by_pid.get(pane_pid, 0)
    if pane_tpgid > 0:
        foreground = [
            entry for entry in non_shell if snapshot.pgid_by_pid.get(entry[1]) == pane_tpgid
        ]
        if foreground:
            return min(foreground, key=lambda entry: (entry[0], entry[1]))[2]

    return max(non_shell, key=lambda entry: (entry[0], entry[1]))[2]


def _tree_cpu_seconds(pane_pid: int, snapshot: ProcessSnapshot) -> float:
    total = snapshot.cpu_seconds_by_pid.get(pane_pid, 0.0)
    for _depth, pid, _command in _descendants(pane_pid, snapshot):
        total += snapshot.cpu_seconds_by_pid.get(pid, 0.0)
    return total


def _tty_is_raw(tty: str) -> bool | None:
    """Whether the program on *tty* reads keystrokes (canonical mode off).

    Interactive programs -- agents, editors, REPLs -- switch their terminal
    to raw mode to read single keys. Servers and builds leave it in
    canonical mode. Returns ``None`` when the tty cannot be inspected.
    """
    if not tty:
        return None
    try:
        fd = os.open(tty, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        attributes = termios.tcgetattr(fd)
    except (OSError, termios.error):
        return None
    finally:
        os.close(fd)
    return not attributes[3] & termios.ICANON


def _int_or_zero(text: str) -> int:
    try:
        return int(text)
    except ValueError:
        return 0


def _list_gd_panes() -> dict[str, PaneSample] | None:
    """The active pane of every ``gd/<repo>/<purpose>/<N>`` session.

    Returns ``None`` when tmux could not be queried, so callers can keep
    their previous knowledge instead of concluding every session vanished.
    A server that is simply not running is not such a failure: tmux exits
    once its last session closes, and then there are no sessions, so the
    listing is empty rather than unknown.
    """
    result = _run_tmux(["list-panes", "-a", "-F", _PANE_LIST_FORMAT], text=True)
    if result.returncode != 0:
        return {} if _tmux_server_is_gone(result.stderr) else None

    panes: dict[str, PaneSample] = {}
    for line in result.stdout.splitlines():
        parts = line.split(_PANE_LIST_SEPARATOR)
        if len(parts) < 6:
            continue
        parts += [""] * (len(_PANE_LIST_FIELDS) - len(parts))
        session_name = parts[0]
        if _parse_gd_session_name(session_name) is None:
            continue
        active = parts[5] == "1"
        if session_name in panes and not active:
            continue
        panes[session_name] = PaneSample(
            session_name=session_name,
            command=parts[1],
            dead=parts[2] == "1",
            pane_pid=_int_or_zero(parts[3]),
            bell=parts[4] == "1",
            tty=parts[6],
            activity=_int_or_zero(parts[7]),
            interactive_hint=parts[8] == "1" or parts[9] == "1",
            agent_state=parts[10].strip(),
            agent_interrupts_unreported=parts[11].strip() == AGENT_INTERRUPTS_UNREPORTED,
            repo_label=parts[12],
            description=parts[13],
        )
    return panes


def _capture_pane_text(session_name: str) -> str | None:
    result = _run_tmux(["capture-pane", "-p", "-t", _active_pane_target(session_name)], text=True)
    if result.returncode != 0:
        return None
    return result.stdout


def _hash_content(text: str) -> str:
    # Identity only -- names a pane's content so changes can be detected.
    # usedforsecurity=False keeps it working where FIPS disables md5.
    return hashlib.md5(text.encode(), usedforsecurity=False).hexdigest()


def _changed_cells(previous: str, current: str, limit: int) -> int:
    """Count differing cells between two captures, stopping once past *limit*."""
    changed = 0
    for old_line, new_line in zip_longest(
        previous.splitlines(), current.splitlines(), fillvalue=""
    ):
        if old_line == new_line:
            continue
        for old_char, new_char in zip_longest(old_line, new_line, fillvalue=" "):
            if old_char != new_char:
                changed += 1
                if changed > limit:
                    return changed
    return changed


def _is_cursor_blink(previous: str, current: str, before_previous: str | None) -> bool:
    """A tiny change that restores the frame from two samples ago.

    Programs that draw their own cursor alternate between two frames that
    differ in one cell. A spinner also touches one cell, but cycles through
    many frames, so it does not flip straight back.
    """
    if before_previous is None or current != before_previous:
        return False
    return _changed_cells(previous, current, _NOISE_MAX_CELLS) <= _NOISE_MAX_CELLS


def resolve_pane_status(
    *,
    dead: bool,
    bell: bool,
    command: str,
    interactive: bool,
    change_age: float,
    cpu_age: float,
) -> str:
    """Classify a pane from agent-agnostic signals.

    * ``dead``: the pane's process exited.
    * ``bell``: a bell rang and nothing has happened since; the program
      asked for attention.
    * ``command``: the foreground program's name, used only to recognise a
      shell prompt.
    * ``interactive``: the program reads keystrokes (raw tty), so being
      quiet means waiting for the user rather than merely idling.
    * ``change_age`` / ``cpu_age``: seconds since the visible content last
      changed / the process tree last consumed CPU.
    """
    if dead:
        return STATUS_IDLE
    if bell:
        return STATUS_WAITING
    if _is_shell(command):
        return STATUS_RUNNING if change_age < _SHELL_ACTIVITY_GRACE_SECS else STATUS_IDLE
    if change_age < _SILENCE_THRESHOLD_SECS or cpu_age < _SILENCE_THRESHOLD_SECS:
        return STATUS_RUNNING
    return STATUS_WAITING if interactive else STATUS_IDLE


@dataclass
class _SessionActivity:
    """Everything the monitor remembers about one session between polls."""

    content: str | None = None
    previous_content: str | None = None
    last_change_time: float = 0.0
    last_activity: int = -1
    #: Recent ``(time, cumulative cpu seconds)`` samples, oldest first.
    cpu_samples: deque[tuple[float, float]] = field(default_factory=lambda: deque(maxlen=16))
    last_cpu_time: float = 0.0
    bell_flag: bool = False
    bell_active: bool = False
    bell_time: float = 0.0
    #: Status last reported by the agent's own hooks ("" when none).
    reported: str = ""
    report_time: float = 0.0
    status: str = STATUS_RUNNING
    details: dict[str, object] = field(default_factory=dict)


class _ControlModeReader:
    """Streams ``%bell`` events for one session over ``tmux -C``."""

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

    def request_stop(self):
        self._running = False
        proc = self._process
        if proc:
            try:
                if proc.stdin is not None:
                    proc.stdin.close()
            except Exception:
                pass
            try:
                proc.terminate()
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def wait_for_stop(self, *, timeout: float = _CONTROL_MODE_STOP_WAIT_SECS):
        proc = self._process
        if proc is None:
            return
        try:
            proc.wait(timeout=timeout)
            return
        except Exception:
            pass
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=_CONTROL_MODE_KILL_WAIT_SECS)
        except Exception:
            logger.debug(
                "control mode reader subprocess %s did not exit after SIGKILL",
                proc.pid,
                exc_info=True,
            )

    def stop(self, *, wait: bool = True, timeout: float = _CONTROL_MODE_STOP_WAIT_SECS):
        self.request_stop()
        if wait:
            self.wait_for_stop(timeout=timeout)

    def is_alive(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    def _run(self):
        try:
            self._process = subprocess.Popen(
                ["tmux", "-C", "attach-session", "-t", f"={self._session_name}", "-r"],
                stdout=subprocess.PIPE,
                stdin=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            for line in self._process.stdout:
                if not self._running:
                    break
                self._parse_line(line.rstrip("\n"))
        except Exception:
            logger.debug("control mode reader for %s died", self._session_name, exc_info=True)
        finally:
            self._running = False
            proc = self._process
            self._process = None
            if proc is not None:
                # Readers are started and torn down for the lifetime of the
                # TUI, so leaving the pipes to the garbage collector leaks a
                # pair of file descriptors per session churn.
                for stream in (proc.stdout, proc.stdin):
                    if stream is not None:
                        try:
                            stream.close()
                        except Exception:
                            pass
                try:
                    proc.terminate()
                    try:
                        proc.wait(timeout=_CONTROL_MODE_STOP_WAIT_SECS)
                    except Exception:
                        try:
                            proc.kill()
                            proc.wait(timeout=_CONTROL_MODE_KILL_WAIT_SECS)
                        except Exception:
                            logger.debug(
                                "control mode reader subprocess %s did not exit",
                                proc.pid,
                                exc_info=True,
                            )
                except Exception:
                    try:
                        proc.kill()
                        proc.wait(timeout=_CONTROL_MODE_KILL_WAIT_SECS)
                    except Exception:
                        logger.debug(
                            "control mode reader subprocess %s failed terminate path",
                            proc.pid,
                            exc_info=True,
                        )

    def _parse_line(self, line: str):
        if line.startswith("%bell"):
            self._callback(self._session_name, "bell")
        elif line.startswith("%exit"):
            self._running = False


class TmuxMonitor:
    """Samples every ``gd/*`` session and keeps an up-to-date status for each.

    :meth:`refresh` performs one sampling round and can be called from any
    thread; :meth:`start` runs it periodically in the background. Statuses
    are read with :meth:`statuses`. Bells arrive both from the ``list-panes``
    flag and, with lower latency, from a control-mode reader per session.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._poll_lock = threading.Lock()
        self._readers: dict[str, _ControlModeReader] = {}
        self._sessions: dict[str, _SessionActivity] = {}
        self._running = False
        self._sync_thread: threading.Thread | None = None
        self._reader_failure_backoff: dict[str, float] = {}
        self._entries: list[dict[str, str]] | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self):
        if self._running:
            return
        self._running = True
        self._sync_thread = threading.Thread(target=self._sync_sessions, daemon=True)
        self._sync_thread.start()

    def stop(self, *, wait: bool = True):
        self._running = False
        readers = list(self._readers.values())
        self._readers.clear()
        for reader in readers:
            reader.request_stop()

        if wait:
            for reader in readers:
                reader.wait_for_stop(timeout=_CONTROL_MODE_STOP_WAIT_SECS)

        sync_thread = self._sync_thread
        self._sync_thread = None
        if (
            wait
            and sync_thread is not None
            and sync_thread is not threading.current_thread()
            and sync_thread.is_alive()
        ):
            sync_thread.join(timeout=3)
            # The sync loop may have attached a reader between the snapshot
            # above and noticing it was asked to stop.
            for reader in list(self._readers.values()):
                reader.stop(wait=False)
            self._readers.clear()

    # -- queries -----------------------------------------------------------

    def statuses(self) -> dict[str, str]:
        with self._lock:
            return {name: activity.status for name, activity in self._sessions.items()}

    def entries(self) -> list[dict[str, str]] | None:
        """Sessions-tab entries from the last sample, or None before the first.

        The same shape as :func:`~.core.list_all_gd_sessions`, without a
        further tmux call: the sample already carried the metadata.
        """
        with self._lock:
            return None if self._entries is None else [dict(e) for e in self._entries]

    def status_for(self, session_name: str) -> str | None:
        with self._lock:
            activity = self._sessions.get(session_name)
            return activity.status if activity is not None else None

    def get_bell_state(self, session_name: str) -> bool:
        with self._lock:
            activity = self._sessions.get(session_name)
            return activity.bell_active if activity is not None else False

    def clear_bell(self, session_name: str):
        with self._lock:
            activity = self._sessions.get(session_name)
            if activity is not None:
                activity.bell_active = False

    # -- sampling ----------------------------------------------------------

    def _on_event(self, session_name: str, event_type: str):
        if event_type != "bell":
            return
        with self._lock:
            activity = self._sessions.setdefault(session_name, _SessionActivity())
            activity.bell_active = True
            activity.bell_time = time.time()
            activity.status = STATUS_WAITING

    def refresh(self) -> dict[str, str]:
        """Sample tmux once and return the resulting statuses."""
        with self._poll_lock:
            try:
                panes = _list_gd_panes()
            except TmuxError:
                logger.debug("tmux pane listing failed", exc_info=True)
                panes = None
            if panes is None:
                return self.statuses()
            snapshot = (
                _get_process_snapshot()
                if any(pane.pane_pid > 0 and not pane.dead for pane in panes.values())
                else ProcessSnapshot.empty()
            )
            now = time.time()
            for pane in panes.values():
                self._sample_session(pane, snapshot, now)
            entries = [
                entry
                for name in sorted(panes)
                if (entry := session_entry(name, panes[name].repo_label, panes[name].description))
            ]
            with self._lock:
                for stale in set(self._sessions) - set(panes):
                    del self._sessions[stale]
                self._entries = entries
        return self.statuses()

    def _sample_session(self, pane: PaneSample, snapshot: ProcessSnapshot, now: float) -> None:
        reported = pane.agent_state if pane.agent_state in AGENT_STATES and not pane.dead else ""
        with self._lock:
            activity = self._sessions.setdefault(pane.session_name, _SessionActivity())
            first_sample = activity.last_activity < 0
            bell_rose = pane.bell and not activity.bell_flag
            activity.bell_flag = pane.bell
            if reported != activity.reported:
                activity.reported = reported
                activity.report_time = now

        if first_sample:
            # tmux's own last-output stamp lets a session that has been quiet
            # for a while classify correctly on the very first sample.
            seed = float(pane.activity) if 0 < pane.activity <= now else now
            activity.last_change_time = seed
            activity.last_cpu_time = seed

        if pane.dead or pane.pane_pid <= 0:
            command = pane.command
            cpu_seconds = None
        else:
            command = _resolve_pane_command(pane.pane_pid, pane.command, snapshot)
            cpu_seconds = _tree_cpu_seconds(pane.pane_pid, snapshot)

        content_changed = False
        if first_sample or pane.activity != activity.last_activity:
            try:
                text = _capture_pane_text(pane.session_name)
            except TmuxError:
                text = None
            if text is not None:
                content_changed = self._record_content(activity, text, now)
        activity.last_activity = pane.activity

        if cpu_seconds is not None and self._cpu_active(activity, cpu_seconds, now):
            activity.last_cpu_time = now

        if reported:
            # The agent reports its own lifecycle; the heuristics only catch
            # a report left behind by an interrupt the agent cannot report.
            with self._lock:
                activity.bell_active = False
                activity.status = (
                    self._reported_status(activity, reported, now)
                    if pane.agent_interrupts_unreported
                    else reported
                )
                activity.details = {"command": command, "source": "agent"}
            return

        interactive = False
        if not pane.dead and not _is_shell(command):
            raw = _tty_is_raw(pane.tty)
            interactive = pane.interactive_hint if raw is None else raw

        with self._lock:
            if bell_rose:
                activity.bell_active = True
                activity.bell_time = now
            elif (
                activity.bell_active
                and content_changed
                and now - activity.bell_time >= _BELL_GRACE_SECS
            ):
                activity.bell_active = False
            activity.status = resolve_pane_status(
                dead=pane.dead,
                bell=activity.bell_active,
                command=command,
                interactive=interactive,
                change_age=now - activity.last_change_time,
                cpu_age=now - activity.last_cpu_time,
            )
            activity.details = {
                "command": command,
                "interactive": interactive,
                "source": "heuristics",
            }

    @staticmethod
    def _reported_status(activity: _SessionActivity, reported: str, now: float) -> str:
        quiet = (
            now - activity.last_change_time >= _AGENT_REPORT_STALE_SECS
            and now - activity.last_cpu_time >= _AGENT_REPORT_STALE_SECS
        )
        if not quiet or now - activity.report_time < _AGENT_REPORT_STALE_SECS:
            return reported
        if reported == STATUS_RUNNING:
            return STATUS_IDLE
        if (
            reported == STATUS_WAITING
            and activity.last_change_time > activity.report_time + _AGENT_REPORT_RENDER_SECS
        ):
            return STATUS_IDLE
        return reported

    @staticmethod
    def _cpu_active(activity: _SessionActivity, cpu_seconds: float, now: float) -> bool:
        """Record a CPU sample; True when the recent window shows real work."""
        samples = activity.cpu_samples
        baseline = None
        for sampled_at, sampled_cpu in samples:
            if sampled_at >= now - _CPU_WINDOW_SECS:
                baseline = sampled_cpu
                break
        if baseline is None and samples:
            baseline = samples[-1][1]
        samples.append((now, cpu_seconds))
        return baseline is not None and cpu_seconds - baseline >= _CPU_ACTIVE_MIN_SECS

    @staticmethod
    def _record_content(activity: _SessionActivity, text: str, now: float) -> bool:
        """Store a capture; return whether it counts as a real visible change."""
        previous = activity.content
        if previous == text:
            return False
        before_previous = activity.previous_content
        activity.previous_content, activity.content = previous, text
        if previous is None:
            return False
        if _is_cursor_blink(previous, text, before_previous):
            return False
        activity.last_change_time = now
        return True

    # -- background loop ---------------------------------------------------

    def _sync_sessions(self):
        while self._running:
            started = time.monotonic()
            try:
                self.refresh()
                self._sync_readers(self.statuses().keys())
            except Exception:
                logger.warning("tmux session monitor poll failed", exc_info=True)

            deadline = started + _POLL_SECS
            while self._running and time.monotonic() < deadline:
                time.sleep(0.1)

    def _sync_readers(self, session_names: Iterable[str]) -> None:
        """Keep one control-mode reader per live ``gd/*`` session."""
        gd_sessions = {s for s in session_names if _parse_gd_session_name(s) is not None}
        current = set(self._readers.keys())

        for s in current - gd_sessions:
            self._remove_reader(s)
            self._reader_failure_backoff.pop(s, None)

        for s in gd_sessions & current:
            reader = self._readers.get(s)
            if reader and not reader.is_alive():
                self._remove_reader(s)
                self._record_reader_failure(s)
            elif reader:
                self._reader_failure_backoff.pop(s, None)

        now = time.time()
        for s in gd_sessions - set(self._readers.keys()):
            if not self._running:
                return
            if now < self._reader_failure_backoff.get(s, 0.0):
                continue
            self._add_reader(s)
            if s in self._readers and not self._readers[s].is_alive():
                self._record_reader_failure(s)
            else:
                self._reader_failure_backoff.pop(s, None)

    def _add_reader(self, session_name: str):
        reader = _ControlModeReader(session_name, self._on_event)
        self._readers[session_name] = reader
        reader.start()

    def _record_reader_failure(self, session_name: str) -> None:
        """Back off ``tmux -C`` attach retries for *session_name*.

        When ``tmux -C attach-session`` fails (most commonly because the
        PTY allocator is exhausted) we don't want to hammer tmux again
        on the next sync iteration. Each failure pushes the next attempt
        out by ``_CONTROL_MODE_FAILURE_BACKOFF_SECS``. Successful
        observation in a subsequent sync clears the entry.
        """
        self._reader_failure_backoff[session_name] = (
            time.time() + _CONTROL_MODE_FAILURE_BACKOFF_SECS
        )

    def _remove_reader(self, session_name: str):
        reader = self._readers.pop(session_name, None)
        if reader:
            reader.stop()


__all__ = [
    "STATUS_IDLE",
    "STATUS_RUNNING",
    "STATUS_WAITING",
    "PaneSample",
    "ProcessSnapshot",
    "TmuxMonitor",
    "launch_command_in_tmux_session",
    "resolve_pane_status",
]
