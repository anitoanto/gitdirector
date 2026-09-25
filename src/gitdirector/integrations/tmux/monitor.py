"""Session status monitoring for ``gd/*`` tmux sessions.

An agent that reports its own status (Claude Code's hooks, OpenCode's
plugin; see ``agents.py``) is trusted as is, filled in only from its own
records where its hooks are silent (see :func:`resolve_agent_status`).

Everything else is classified from signals every terminal program exposes,
so the same rules apply to a shell, a build, a dev server, or any other
agent:

* the process tree under the pane (is an interactive shell at its prompt?)
* whether the visible pane content changed recently
* whether the process tree consumed CPU recently
* the terminal bell

See :func:`resolve_pane_status` for how they combine.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from itertools import zip_longest
from pathlib import Path

from ...agents import (
    AGENT_STATE_OPTION,
    AGENT_STATES,
    AGENT_TRANSCRIPT_OPTION,
    AGENT_WAITER_OPTION,
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
    respawn_pane,
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
        f"tmux kill-session -g -t {quoted_session_target} >/dev/null 2>&1 || true; "
        f"rm -f {ready_marker_quoted} >/dev/null 2>&1 || true; "
        "exit $status"
    )
    try:
        respawn_pane(
            pane_target,
            # Only the command's own shell is a login shell; a second one
            # would run the user's profile twice before every launch.
            _tmux_child_environment_command(f"sh -c {shlex.quote(cleanup_script)}"),
        )
    except TmuxError:
        kill_tmux_session(session_name)
        ready_marker.unlink(missing_ok=True)
        raise
    return ready_marker


_SHELL_COMMANDS = frozenset({"zsh", "bash", "fish", "sh", "dash", "tcsh", "csh", "ksh"})

STATUS_WAITING = "waiting"
STATUS_RUNNING = "running"
STATUS_IDLE = "idle"

# How often the monitor samples tmux.
_POLL_SECS = 1.0
# A program whose visible output and CPU use have both been quiet for this
# long is no longer working.
_SILENCE_THRESHOLD_SECS = 4.0
# A program redraws itself after its pane is resized (a client attaching
# at another size); changes seen this soon after a resize are that redraw.
_RESIZE_REDRAW_SECS = 1.5
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

# An approved tool shows as a process the agent started after asking; one
# that lived this long is the tool, not a short-lived hook of the user's.
_APPROVED_TOOL_MIN_SECS = 2.0
# ps start times are whole seconds.
_PROCESS_START_SLACK_SECS = 1.0
# Claude Code keeps the machine awake with this while it works.
_AGENT_HELPERS = frozenset({"caffeinate"})
# Where Claude Code's transcript records an interrupt.
_INTERRUPT_MARKER = "[Request interrupted by user"
# How much of the transcript's end is read for it.
_TRANSCRIPT_TAIL_BYTES = 64 * 1024

_PANE_LIST_SEPARATOR = "\t"
# The user-editable description is last and the line is split at most this
# many times, so a separator inside it can never shift another field.
_PANE_LIST_FIELDS = (
    ("session", "#{session_name}"),
    ("command", "#{pane_current_command}"),
    ("dead", "#{pane_dead}"),
    ("pid", "#{pane_pid}"),
    ("bell", "#{window_bell_flag}"),
    ("pane_active", "#{pane_active}"),
    ("window_active", "#{window_active}"),
    ("pane_id", "#{pane_id}"),
    ("tty", "#{pane_tty}"),
    ("activity", "#{window_activity}"),
    ("size", "#{pane_width}x#{pane_height}"),
    ("agent_state", f"#{{{AGENT_STATE_OPTION}}}"),
    ("agent_waiter", f"#{{{AGENT_WAITER_OPTION}}}"),
    ("agent_transcript", f"#{{{AGENT_TRANSCRIPT_OPTION}}}"),
    ("repo_label", f"#{{{GD_REPO_LABEL_OPTION}}}"),
    ("description", f"#{{{GD_DESCRIPTION_OPTION}}}"),
)
_PANE_LIST_NAMES = tuple(name for name, _ in _PANE_LIST_FIELDS)
_PANE_LIST_FORMAT = _PANE_LIST_SEPARATOR.join(fmt for _, fmt in _PANE_LIST_FIELDS)


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
    #: ``<width>x<height>`` of the pane.
    size: str = ""
    #: Raw self-report from the agent's hooks: a status, optionally followed
    #: by the epoch it was made ("" when none).
    agent_state: str = ""
    #: ``<agent id> <epoch>`` of a subagent blocked on the user ("" when none).
    agent_waiter: str = ""
    #: The agent's transcript, when it keeps one we read ("" otherwise).
    agent_transcript: str = ""
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
    #: Seconds since each process started.
    elapsed_by_pid: dict[int, float] = field(default_factory=dict)
    #: Full command line of each process.
    args_by_pid: dict[int, str] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> ProcessSnapshot:
        return cls({}, {}, {}, {}, {}, {}, {})


def _normalize_process_command(raw_args: str) -> str:
    token = raw_args.strip().split(" ", 1)[0]
    if not token:
        return ""
    return Path(token).name


def _parse_cpu_seconds(text: str) -> float:
    """Parse ``ps`` ``time`` or ``etime`` output: ``[[dd-]hh:]mm:ss[.cc]``."""
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


_PS_ROW_RE = re.compile(r"\s*(\d+)\s+(\d+)\s+(-?\d+)\s+(-?\d+)\s+(\S+)\s+(\S+)\s+(.*)")


def _get_process_snapshot() -> ProcessSnapshot:
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,pgid=,tpgid=,time=,etime=,args="],
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
        snapshot.elapsed_by_pid[pid] = _parse_cpu_seconds(match.group(6))
        snapshot.commands_by_pid[pid] = _normalize_process_command(match.group(7))
        snapshot.args_by_pid[pid] = match.group(7).strip()
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


def _is_interactive_shell(args: str) -> bool:
    """Whether *args* start a shell that reads commands from its terminal.

    Only options may follow the shell's name: a ``-c`` string or a script
    operand makes it a program like any other.
    """
    words = args.split()
    if not words or not _is_shell(Path(words[0]).name):
        return False
    for word in words[1:]:
        if not word.startswith("-") or (not word.startswith("--") and "c" in word[1:]):
            return False
    return True


def _prompt_shell(pane_pid: int, snapshot: ProcessSnapshot) -> str | None:
    """The interactive shell whose prompt holds the pane's terminal, if any.

    Such a shell gives every command the user runs a process group of its
    own and hands it the terminal. Whatever runs in the shell's own group
    while it holds the terminal (profile scripts, prompt helpers, command
    substitutions) is the shell preparing its prompt, not a job.
    """
    leader = snapshot.tpgid_by_pid.get(pane_pid, 0)
    if leader <= 0 or not _is_interactive_shell(snapshot.args_by_pid.get(leader, "")):
        return None
    return snapshot.commands_by_pid.get(leader)


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


def _int_or_zero(text: str) -> int:
    try:
        return int(text)
    except ValueError:
        return 0


def _parse_agent_report(value: str) -> tuple[str, float | None]:
    """``(state, epoch)`` from a raw report; state is "" when not a known one."""
    state, _, stamp = value.strip().partition(" ")
    if state not in AGENT_STATES:
        return "", None
    try:
        return state, float(stamp) if stamp else None
    except ValueError:
        return state, None


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

    rows = []
    for line in result.stdout.splitlines():
        values = line.split(_PANE_LIST_SEPARATOR, len(_PANE_LIST_NAMES) - 1)
        if len(values) == len(_PANE_LIST_NAMES):
            rows.append(dict(zip(_PANE_LIST_NAMES, values)))
    panes: dict[str, PaneSample] = {}
    for row in rows:
        session_name = row["session"]
        if _parse_gd_session_name(session_name) is None:
            continue
        # pane_active is per window; the pane that counts (and that
        # capture-pane reads) is the active one of the current window.
        current = row["pane_active"] == "1" and row["window_active"] == "1"
        if session_name in panes and not current:
            continue
        panes[session_name] = PaneSample(
            session_name=session_name,
            command=row["command"],
            dead=row["dead"] == "1",
            pane_pid=_int_or_zero(row["pid"]),
            bell=row["bell"] == "1",
            tty=row["tty"],
            activity=_int_or_zero(row["activity"]),
            size=row["size"],
            agent_state=row["agent_state"].strip(),
            agent_waiter=row["agent_waiter"].strip(),
            agent_transcript=row["agent_transcript"].strip(),
            repo_label=row["repo_label"],
            description=row["description"],
        )
    return panes


def _capture_pane_text(session_name: str) -> str | None:
    result = _run_tmux(["capture-pane", "-p", "-t", _active_pane_target(session_name)], text=True)
    if result.returncode != 0:
        return None
    return result.stdout


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


def _ignores_bell(session_name: str) -> bool:
    """Whether a bell in this session says nothing about someone being needed.

    A plain shell session is either running a command or idle at its prompt:
    its bells (a failed completion, a job that finished) never mean waiting.
    """
    parsed = _parse_gd_session_name(session_name)
    return parsed is not None and parsed[1] == "shell"


def resolve_pane_status(
    *,
    dead: bool,
    bell: bool,
    at_prompt: bool,
    change_age: float,
    cpu_age: float,
) -> str:
    """Classify a pane from agent-agnostic signals.

    * ``dead``: the pane's process exited.
    * ``bell``: a bell rang and nothing has happened since; the program
      asked for someone's attention, the one sign of waiting any terminal
      program can give.
    * ``at_prompt``: an interactive shell holds the terminal, so no job is
      running; what changes on screen is the prompt itself (drawing it,
      typing, redrawing it) or output of a command that already finished.
    * ``change_age`` / ``cpu_age``: seconds since the visible content last
      changed / the process tree last consumed CPU.
    """
    if dead:
        return STATUS_IDLE
    if bell:
        return STATUS_WAITING
    if at_prompt:
        return STATUS_IDLE
    if change_age < _SILENCE_THRESHOLD_SECS or cpu_age < _SILENCE_THRESHOLD_SECS:
        return STATUS_RUNNING
    return STATUS_IDLE


def _parse_stamped(value: str) -> tuple[str, float | None]:
    """``(word, epoch)`` from ``"<word> <epoch>"``; epoch is None when missing."""
    word, _, stamp = value.strip().partition(" ")
    try:
        return word, float(stamp) if stamp else None
    except ValueError:
        return word, None


def _parse_iso_epoch(text: object) -> float | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _last_interrupt(path: str) -> float | None:
    """When Claude Code's transcript last recorded an interrupt, if it did lately.

    Escape (during a turn or at a prompt) fires no hook, but Claude writes a
    ``[Request interrupted by user...]`` entry the moment it happens. Only
    the transcript's end is read: anything the agent did after an interrupt
    fired hooks that report a newer status anyway.
    """
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - _TRANSCRIPT_TAIL_BYTES))
            tail = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    for line in reversed(tail.splitlines()):
        if _INTERRUPT_MARKER not in line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        message = entry.get("message") if isinstance(entry, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        texts = (
            [content]
            if isinstance(content, str)
            else [part.get("text", "") for part in content if isinstance(part, dict)]
            if isinstance(content, list)
            else []
        )
        if entry.get("type") == "user" and any(
            isinstance(text, str) and text.startswith(_INTERRUPT_MARKER) for text in texts
        ):
            return _parse_iso_epoch(entry.get("timestamp"))
    return None


def _started_a_tool_since(
    pane_pid: int, snapshot: ProcessSnapshot, since: float, now: float
) -> bool:
    """Whether the agent under *pane_pid* started a lasting process after *since*.

    Approving a permission prompt fires no hook until the tool finishes, but
    the tool runs as a new child process of the agent (Claude Code's Bash
    tool starts a shell); a hook of the user's own is gone within moments.
    """
    for _depth, pid, command in _descendants(pane_pid, snapshot):
        if command in _AGENT_HELPERS:
            continue
        elapsed = snapshot.elapsed_by_pid.get(pid)
        if elapsed is None or elapsed < _APPROVED_TOOL_MIN_SECS:
            continue
        if now - elapsed >= since - _PROCESS_START_SLACK_SECS:
            return True
    return False


def resolve_agent_status(
    *,
    reported: str,
    reported_at: float,
    waiter_at: float | None,
    interrupted_at: float | None,
    started_tool: bool,
) -> str:
    """The status of an agent that reports its own, filled in where it is silent.

    * A subagent blocked on the user (*waiter_at*) makes the session
      waiting, whatever the main thread reported.
    * An interrupt recorded after the last report (*interrupted_at*) ended
      the turn, or dismissed the prompt: idle.
    * A waiting that was followed by the agent starting a tool
      (*started_tool*) was approved: the tool is running (a subagent's
      approval hands back to the main thread's status).
    """
    status, since = (
        (STATUS_WAITING, waiter_at) if waiter_at is not None else (reported, reported_at)
    )
    if status == STATUS_IDLE:
        return STATUS_IDLE
    if interrupted_at is not None and interrupted_at > since:
        return STATUS_IDLE
    if status == STATUS_WAITING and started_tool:
        return reported if waiter_at is not None else STATUS_RUNNING
    return status


@dataclass
class _SessionActivity:
    """Everything the monitor remembers about one session between polls."""

    content: str | None = None
    previous_content: str | None = None
    last_change_time: float = 0.0
    last_activity: int = -1
    size: str = ""
    resize_time: float = float("-inf")
    #: Recent ``(time, cumulative cpu seconds)`` samples, oldest first.
    cpu_samples: deque[tuple[float, float]] = field(default_factory=lambda: deque(maxlen=16))
    last_cpu_time: float = 0.0
    bell_flag: bool = False
    bell_active: bool = False
    bell_time: float = 0.0
    #: Status last reported by the agent's own hooks ("" when none), the raw
    #: value it was parsed from, and when it was made (the report's own stamp,
    #: else the sample that first saw it).
    reported: str = ""
    report_raw: str = ""
    report_time: float = 0.0
    #: The transcript's size and mtime when it was last read, and what it said.
    transcript_key: tuple | None = None
    interrupted_at: float | None = None
    status: str = STATUS_RUNNING


class TmuxMonitor:
    """Samples every ``gd/*`` session and keeps an up-to-date status for each.

    :meth:`refresh` performs one sampling round and can be called from any
    thread; :meth:`start` runs it periodically in the background. Statuses
    are read with :meth:`statuses`.

    Bells come from ``list-panes``' bell flag, which tmux only raises while
    no client is attached to the session -- so nothing here may attach one
    (a read-only client would also make tmux refuse ``send-keys``).
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._poll_lock = threading.Lock()
        self._sessions: dict[str, _SessionActivity] = {}
        self._sync_thread: threading.Thread | None = None
        # One event per start: a thread from an earlier start that has not
        # noticed its stop yet can never be revived by a later start.
        self._stop_event: threading.Event | None = None
        self._entries: list[dict[str, str]] | None = None
        # A sample describes tmux as it was when the sample began. Each local
        # change (a session created, described, removed) bumps the
        # generation; a sample begun before the latest bump is withheld, so
        # it can never undo what the console already shows.
        self._generation = 0
        self._entries_generation = 0
        self._wake = threading.Event()

    # -- lifecycle ---------------------------------------------------------

    def start(self):
        if self._stop_event is not None:
            return
        # Whatever was sampled before the stop is old news.
        self.invalidate()
        stop_event = threading.Event()
        self._stop_event = stop_event
        self._sync_thread = threading.Thread(
            target=self._sync_sessions, args=(stop_event,), daemon=True
        )
        self._sync_thread.start()

    def stop(self, *, wait: bool = True):
        stop_event, self._stop_event = self._stop_event, None
        sync_thread, self._sync_thread = self._sync_thread, None
        if stop_event is not None:
            stop_event.set()
            self._wake.set()
        if (
            wait
            and sync_thread is not None
            and sync_thread is not threading.current_thread()
            and sync_thread.is_alive()
        ):
            sync_thread.join(timeout=3)

    # -- queries -----------------------------------------------------------

    def statuses(self) -> dict[str, str]:
        with self._lock:
            return {name: activity.status for name, activity in self._sessions.items()}

    def entries(self) -> list[dict[str, str]] | None:
        """Sessions-tab entries from the last sample, or None when there is none
        begun since the last :meth:`invalidate`.

        The same shape as :func:`~.core.list_all_gd_sessions`, without a
        further tmux call: the sample already carried the metadata.
        """
        with self._lock:
            if self._entries is None or self._entries_generation < self._generation:
                return None
            return [dict(e) for e in self._entries]

    def invalidate(self) -> None:
        """Say tmux just changed here: drop older samples and take a new one now."""
        with self._lock:
            self._generation += 1
        self._wake.set()

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

    def refresh(self) -> dict[str, str]:
        """Sample tmux once and return the resulting statuses."""
        with self._poll_lock:
            with self._lock:
                generation = self._generation
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
                self._entries_generation = generation
        return self.statuses()

    def _sample_session(self, pane: PaneSample, snapshot: ProcessSnapshot, now: float) -> None:
        reported, stamp = _parse_agent_report(pane.agent_state) if not pane.dead else ("", None)
        prompt_shell = None
        if pane.dead or pane.pane_pid <= 0:
            command = pane.command
            cpu_seconds = None
        else:
            prompt_shell = _prompt_shell(pane.pane_pid, snapshot)
            command = prompt_shell or _resolve_pane_command(pane.pane_pid, pane.command, snapshot)
            cpu_seconds = _tree_cpu_seconds(pane.pane_pid, snapshot)
        # A report outlives an agent that exited without saying so.
        if reported and _is_shell(command):
            reported = ""

        with self._lock:
            activity = self._sessions.setdefault(pane.session_name, _SessionActivity())
            first_sample = activity.last_activity < 0
            bell_rose = (
                pane.bell and not activity.bell_flag and not _ignores_bell(pane.session_name)
            )
            activity.bell_flag = pane.bell
            if pane.agent_state != activity.report_raw or reported != activity.reported:
                activity.report_raw = pane.agent_state
                activity.reported = reported
                activity.report_time = stamp if stamp is not None else now

        if reported:
            status = self._agent_status(pane, activity, snapshot, now)
            with self._lock:
                activity.bell_active = False
                activity.status = status
            # The pane is only watched again once the agent stops reporting.
            activity.last_activity = -1
            activity.content = activity.previous_content = None
            return

        if first_sample:
            # tmux's own last-output stamp lets a session that has been quiet
            # for a while classify correctly on the very first sample.
            seed = float(pane.activity) if 0 < pane.activity <= now else now
            activity.last_change_time = seed
            activity.last_cpu_time = seed

        if not first_sample and pane.size != activity.size:
            activity.resize_time = now
        activity.size = pane.size

        content_changed = False
        if first_sample or pane.activity != activity.last_activity:
            try:
                text = _capture_pane_text(pane.session_name)
            except TmuxError:
                text = None
            if text is not None:
                content_changed = self._record_content(
                    activity, text, now, counts=now - activity.resize_time > _RESIZE_REDRAW_SECS
                )
        activity.last_activity = pane.activity

        if cpu_seconds is not None and self._cpu_active(activity, cpu_seconds, now):
            activity.last_cpu_time = now

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
                at_prompt=prompt_shell is not None,
                change_age=now - activity.last_change_time,
                cpu_age=now - activity.last_cpu_time,
            )

    @staticmethod
    def _agent_status(
        pane: PaneSample, activity: _SessionActivity, snapshot: ProcessSnapshot, now: float
    ) -> str:
        waiter, waiter_at = _parse_stamped(pane.agent_waiter)
        if not waiter:
            waiter_at = None
        elif waiter_at is None:
            waiter_at = activity.report_time
        interrupted_at = None
        if pane.agent_transcript and (activity.reported != STATUS_IDLE or waiter_at is not None):
            interrupted_at = TmuxMonitor._transcript_interrupt(activity, pane.agent_transcript)
        waiting = waiter_at is not None or activity.reported == STATUS_WAITING
        since = waiter_at if waiter_at is not None else activity.report_time
        return resolve_agent_status(
            reported=activity.reported,
            reported_at=activity.report_time,
            waiter_at=waiter_at,
            interrupted_at=interrupted_at,
            started_tool=waiting
            and pane.pane_pid > 0
            and _started_a_tool_since(pane.pane_pid, snapshot, since, now),
        )

    @staticmethod
    def _transcript_interrupt(activity: _SessionActivity, path: str) -> float | None:
        """The transcript's last interrupt, re-read only when the file changed."""
        try:
            info = os.stat(path)
        except OSError:
            return None
        key = (path, info.st_size, info.st_mtime_ns)
        if key != activity.transcript_key:
            activity.transcript_key = key
            activity.interrupted_at = _last_interrupt(path)
        return activity.interrupted_at

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
    def _record_content(
        activity: _SessionActivity, text: str, now: float, *, counts: bool = True
    ) -> bool:
        """Store a capture; return whether it counts as a real visible change."""
        previous = activity.content
        if previous == text:
            return False
        before_previous = activity.previous_content
        activity.previous_content, activity.content = previous, text
        if previous is None or not counts:
            return False
        if _is_cursor_blink(previous, text, before_previous):
            return False
        activity.last_change_time = now
        return True

    # -- background loop ---------------------------------------------------

    def _sync_sessions(self, stop_event: threading.Event):
        while not stop_event.is_set():
            started = time.monotonic()
            self._wake.clear()
            try:
                self.refresh()
            except Exception:
                logger.warning("tmux session monitor poll failed", exc_info=True)
            # invalidate() cuts the wait short; stop() sets both.
            self._wake.wait(max(0.0, started + _POLL_SECS - time.monotonic()))


__all__ = [
    "STATUS_IDLE",
    "STATUS_RUNNING",
    "STATUS_WAITING",
    "PaneSample",
    "ProcessSnapshot",
    "TmuxMonitor",
    "launch_command_in_tmux_session",
    "resolve_agent_status",
    "resolve_pane_status",
]
