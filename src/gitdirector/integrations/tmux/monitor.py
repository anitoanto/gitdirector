import hashlib
import logging
import os
import re
import shlex
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from .core import (
    TmuxError,
    _active_pane_target,
    _list_sessions,
    _parse_gd_session_name,
    _run_tmux,
    kill_tmux_session,
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


def _agent_done_marker(ready_marker: Path) -> Path:
    return Path(f"{ready_marker}.done")


def _agent_failed_marker(ready_marker: Path) -> Path:
    return Path(f"{ready_marker}.failed")


def launch_command_in_tmux_session(session_name: str, command: str) -> Path:
    """Run *command* in *session_name* and self-destruct the session on exit.

    The command is started by respawning the active pane, avoiding races with
    the fresh session's interactive shell startup. When the command exits, the
    wrapping shell detaches any attached client and kills the session so the
    lifecycle matches a one-shot invocation.

    A temporary marker path is returned so callers that need to wait on a
    startup signal (such as the TUI's agent loading screen) can do so. The
    marker is left for the caller to consume, which avoids fast-exit races.
    """
    ready_marker = _make_agent_ready_marker()
    ready_marker_quoted = shlex.quote(str(ready_marker))
    done_marker = _agent_done_marker(ready_marker)
    failed_marker = _agent_failed_marker(ready_marker)
    done_marker_quoted = shlex.quote(str(done_marker))
    failed_marker_quoted = shlex.quote(str(failed_marker))
    pane_target = _active_pane_target(session_name)
    quoted_session_target = shlex.quote(f"={session_name}")
    command_script = f"sh -lc {shlex.quote(command)}"
    cleanup_script = (
        "clear; "
        f"touch {ready_marker_quoted} >/dev/null 2>&1 || true; "
        f"{command_script}; "
        "status=$?; "
        f'if [ "$status" -eq 0 ]; then touch {done_marker_quoted} >/dev/null 2>&1 || true; '
        f"else printf '%s\\n' \"$status\" > {failed_marker_quoted} 2>/dev/null || true; "
        f"touch {done_marker_quoted} >/dev/null 2>&1 || true; fi; "
        f"tmux detach-client -s {quoted_session_target} >/dev/null 2>&1 || true; "
        f"tmux kill-session -t {quoted_session_target} >/dev/null 2>&1 || true; "
        f"rm -f {ready_marker_quoted} {done_marker_quoted} {failed_marker_quoted} >/dev/null 2>&1 || true; "
        "exit $status"
    )
    result = _run_tmux(
        [
            "respawn-pane",
            "-k",
            "-t",
            pane_target,
            f"sh -lc {shlex.quote(cleanup_script)}",
        ],
    )
    if isinstance(result.returncode, int) and result.returncode != 0:
        kill_tmux_session(session_name)
        for marker in (ready_marker, done_marker, failed_marker):
            marker.unlink(missing_ok=True)
        raise TmuxError(
            "tmux respawn-pane failed",
            args_list=list(result.args) if isinstance(result.args, list) else None,
            returncode=result.returncode,
            stderr=result.stderr,
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

_AGENT_PURPOSES = frozenset({"opencode", "claude", "copilot", "codex", "pi"})

_SILENCE_THRESHOLD_SECS = 10
_OUTPUT_ACTIVITY_GRACE_SECS = 4.0
_BELL_GRACE_SECS = 1.0
_CONTENT_POLL_SECS = 3
_CONTROL_MODE_STOP_WAIT_SECS = 5.0
_CONTROL_MODE_KILL_WAIT_SECS = 2.0
_CONTROL_MODE_FAILURE_BACKOFF_SECS = 30.0


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
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,pgid=,tpgid=,args="],
            capture_output=True,
            text=True,
        )
    except Exception:
        return {}, {}, {}, {}
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
            "#{session_name}|#{pane_current_command}|#{pane_dead}|#{pane_pid}|#{window_bell_flag}|#{pane_active}",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return {}

    pane_rows: list[tuple[str, str, bool, int, bool, str, bool]] = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split("|", 5)
        if len(parts) < 5:
            continue
        session_name = parts[0]
        parsed = _parse_gd_session_name(session_name)
        if parsed is None:
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
                parts[4] == "1",
                parsed[1],
                len(parts) < 6 or parts[5] == "1",
            )
        )

    if not pane_rows:
        return {}

    needs_process_snapshot = any(
        pane_pid > 0
        for _session_name, _command, _dead, pane_pid, _bell, _purpose, _active in pane_rows
    )
    if needs_process_snapshot:
        children_by_parent, commands_by_pid, pgid_by_pid, tpgid_by_pid = _get_process_snapshot()
    else:
        children_by_parent, commands_by_pid, pgid_by_pid, tpgid_by_pid = {}, {}, {}, {}
    statuses: dict[str, dict[str, object]] = {}
    for session_name, command, dead, pane_pid, bell, purpose, active in pane_rows:
        if session_name in statuses and not active:
            continue
        effective_command = command
        if needs_process_snapshot and pane_pid > 0:
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
            "bell": bell,
        }
    return statuses


def resolve_pane_status(
    purpose: str,
    command: str,
    dead: bool,
    *,
    bell: bool = False,
    last_output_time: float = 0.0,
    last_content_change_time: float = 0.0,
) -> str:
    """Determine pane status from tmux and monitor info.

    Returns "waiting", "running", or "idle".
    """
    if bell:
        return "waiting"
    if dead:
        return "idle"
    now = time.time()
    clean_cmd = command.lstrip("-")
    is_shell = clean_cmd in _SHELL_COMMANDS
    if is_shell:
        if last_output_time > 0 and now - last_output_time < _OUTPUT_ACTIVITY_GRACE_SECS:
            return "running"
        return "idle"
    last_change = last_content_change_time or last_output_time
    if purpose in _AGENT_PURPOSES and clean_cmd == purpose and last_change > 0:
        elapsed = now - last_change
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
        self._reader_failure_backoff: dict[str, float] = {}

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
                    next_attempt = self._reader_failure_backoff.get(s, 0.0)
                    if now < next_attempt:
                        continue
                    self._add_reader(s)
                    if s in self._readers and not self._readers[s].is_alive():
                        self._record_reader_failure(s)
                    else:
                        self._reader_failure_backoff.pop(s, None)

                self._poll_content_changes(gd_sessions)
            except Exception:
                logger.warning("tmux session sync failed", exc_info=True)

            for _ in range(_CONTENT_POLL_SECS * 10):
                if not self._running:
                    return
                time.sleep(0.1)

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

    def _poll_content_changes(self, sessions: set[str]):
        for session_name in sessions:
            parsed = _parse_gd_session_name(session_name)
            if parsed is None or parsed[1] not in _AGENT_PURPOSES:
                continue
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


__all__ = [name for name in globals() if not name.startswith("__")]
