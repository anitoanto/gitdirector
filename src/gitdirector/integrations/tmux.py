"""tmux integration via subprocess."""

import hashlib
import os
import re
import shlex
import subprocess
import tempfile
import threading
import time
from pathlib import Path


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
        ["tmux", "has-session", "-t", session_name],
        capture_output=True,
    )
    return result.returncode == 0


def list_repo_sessions(repo_name: str) -> list[str]:
    """List all tmux sessions for a given repository."""
    clean = _sanitize_repo_name(repo_name)
    prefix = f"gd/{clean}/"
    sessions = _list_sessions()
    return sorted([s for s in sessions if s.startswith(prefix)])


def list_all_gd_sessions() -> list[dict[str, str]]:
    """List all GitDirector tmux sessions (gd/ prefix).

    Returns a list of dicts with keys: session_name, repo, purpose.
    """
    sessions = _list_sessions()
    entries = []
    for s in sorted(sessions):
        if not s.startswith("gd/"):
            continue
        parts = s.split("/")
        if len(parts) < 4:
            continue
        repo = parts[1]
        purpose = parts[2]
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
    return session_name


def kill_tmux_session(session_name: str) -> bool:
    """Kill a tmux session. Returns True on success."""
    result = subprocess.run(
        ["tmux", "kill-session", "-t", session_name],
        capture_output=True,
    )
    return result.returncode == 0


def attach_tmux_session(session_name: str) -> None:
    """Attach to an existing tmux session, blocking until detach/exit."""
    if os.environ.get("TMUX"):
        subprocess.run(["tmux", "switch-client", "-t", session_name])
    else:
        subprocess.run(["tmux", "attach-session", "-t", session_name])


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


def make_panel_session_name(panel_name: str) -> str:
    return f"gd/panel/{_sanitize_panel_name(panel_name)}"


def kill_panel_tmux_session(panel_name: str) -> bool:
    return kill_tmux_session(make_panel_session_name(panel_name))


def _tmux_output(*args: str) -> str:
    result = subprocess.run(
        ["tmux", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


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
        )


def _list_window_panes_row_major(session_name: str) -> list[str]:
    output = _tmux_output(
        "list-panes",
        "-t",
        f"{session_name}:0",
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
    root_target = f"{session_name}:0.0"
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
        )
        _split_panel_row(row_target, cols)

    return _list_window_panes_row_major(session_name)


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


def _configure_panel_window(session_name: str, pane_ids: list[str]) -> None:
    window_target = f"{session_name}:0"
    subprocess.run(
        ["tmux", "set-window-option", "-t", window_target, "pane-base-index", "1"],
        check=True,
    )
    subprocess.run(
        ["tmux", "set-window-option", "-t", window_target, "pane-border-status", "top"],
        check=True,
    )
    subprocess.run(
        [
            "tmux",
            "set-window-option",
            "-t",
            window_target,
            "pane-border-format",
            "#{?pane_active,#[bold fg=black bg=cyan],#[fg=colour250 bg=colour236]} Pane #{pane_title} #[default]",
        ],
        check=True,
    )

    for pane_number, pane_id in enumerate(pane_ids, start=1):
        subprocess.run(
            ["tmux", "select-pane", "-t", pane_id, "-T", str(pane_number)],
            check=True,
        )


def _panel_pane_command(panel_name: str, pane_index: int, session_name: str | None) -> str:
    if session_name:
        quoted_session = shlex.quote(session_name)
        detached_message = _printf_lines_command(
            [
                f"Panel: {panel_name}",
                f"Pane {pane_index}: detached from {session_name}",
                "Reopen the panel from GitDirector to attach again.",
            ]
        )
        missing_message = _printf_lines_command(
            [
                f"Panel: {panel_name}",
                f"Pane {pane_index}: missing session",
                session_name,
            ]
        )
        script = (
            "clear; "
            f"if tmux has-session -t {quoted_session} >/dev/null 2>&1; then "
            f"env -u TMUX tmux attach-session -t {quoted_session}; "
            f"clear; {detached_message}; "
            "else "
            f"{missing_message}; "
            "fi; "
            "exec tail -f /dev/null"
        )
    else:
        script = (
            "clear; "
            f"{_printf_lines_command([f'Panel: {panel_name}', f'Pane {pane_index}: unassigned'])}; "
            "exec tail -f /dev/null"
        )
    return f"sh -c {shlex.quote(script)}"


def rebuild_panel_tmux_session(
    panel_name: str,
    rows: int,
    cols: int,
    panes: dict[int, str | None],
) -> str:
    session_name = make_panel_session_name(panel_name)
    if _session_exists(session_name):
        subprocess.run(["tmux", "kill-session", "-t", session_name], check=False)

    subprocess.run(
        [
            "tmux",
            "new-session",
            "-d",
            "-s",
            session_name,
            "-n",
            panel_name,
            "-c",
            str(Path.home()),
        ],
        check=True,
    )

    pane_ids = _build_panel_grid(session_name, rows, cols)
    _configure_panel_window(session_name, pane_ids)
    _ensure_panel_prefix_bindings()
    total_panes = rows * cols
    for pane_index, pane_id in enumerate(pane_ids[:total_panes], start=1):
        subprocess.run(
            [
                "tmux",
                "respawn-pane",
                "-k",
                "-t",
                pane_id,
                _panel_pane_command(panel_name, pane_index, panes.get(pane_index)),
            ],
            check=True,
        )

    return session_name


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
    cleanup_script = (
        f"touch {ready_marker_quoted} >/dev/null 2>&1 || true; "
        "clear; "
        f"{normalized_agent_cmd}; "
        "status=$?; "
        f"rm -f {ready_marker_quoted} >/dev/null 2>&1 || true; "
        "tmux detach-client >/dev/null 2>&1 || true; "
        f"tmux kill-session -t {shlex.quote(session_name)} >/dev/null 2>&1 || true; "
        "exit $status"
    )
    subprocess.run(
        [
            "tmux",
            "send-keys",
            "-t",
            session_name,
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
        if not line or not line.startswith("gd/"):
            continue
        parts = line.split("|", 3)
        if len(parts) < 4:
            continue
        try:
            pane_pid = int(parts[3])
        except (ValueError, IndexError):
            pane_pid = 0
        pane_rows.append(
            (
                parts[0],
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
        ["tmux", "capture-pane", "-p", "-t", session_name],
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
                ["tmux", "-C", "attach-session", "-t", self._session_name, "-r"],
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
                gd_sessions = {s for s in sessions if s.startswith("gd/")}
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
