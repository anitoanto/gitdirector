"""Stress gitdirector's tmux integration against a real tmux server.

One phase per process:

  normal     heavy concurrent churn, no resource limits
  ptystarve  /dev/pts capped just above what is in use, plus a pty hog that
             holds a changing number of ptys: pane spawns fail intermittently,
             the way they do on a Mac that is out of ptys
  nproc      the tmux server runs under a tight RLIMIT_NPROC, plus a process
             hog: forks fail intermittently
  chaos      normal churn while pane programs and attached clients are killed,
             windows resized and panes respawned under the workers' feet

Workers drive the real APIs (sessions, descriptions, agent launches, panels,
decks with the real sidebar app and attached clients, the monitor and its
invalidation, config sync) while a watchdog checks that the tmux server never
dies and no pane is left broken. A phase passes only if, besides that, every
resource failure was explained to the user, and killing the server left no
process and no pty behind.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import resource
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from collections import Counter, deque
from pathlib import Path

KEEPALIVE = "stress-keepalive"
RESOURCE_MARKERS = (
    "fork failed",
    "No space left on device",
    "Resource temporarily unavailable",
    "Device not configured",
    "No more ptys",
    "open terminal failed",
    "Too many open files",
)


def sh(*args: str, timeout: float = 10) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def tmux(*args: str) -> subprocess.CompletedProcess[str]:
    return sh("tmux", *args)


def pts_in_use() -> int:
    return len([n for n in os.listdir("/dev/pts") if n.isdigit()])


def set_pts_max(value: int) -> None:
    result = sh("sudo", "-n", "mount", "-o", f"remount,max={value}", "/dev/pts")
    if result.returncode != 0:
        raise SystemExit(f"cannot remount /dev/pts: {result.stderr.strip()}")


class Run:
    def __init__(self, phase: str, seconds: float, out: Path) -> None:
        self.phase, self.seconds, self.out = phase, seconds, out
        self.stop = threading.Event()
        self.crashed: dict | None = None
        self.ops: deque = deque(maxlen=300)
        self.counts: Counter = Counter()
        self.errors: Counter = Counter()
        self.unexpected: dict[str, str] = {}
        self.broken_panes_seen: set[str] = set()
        self.slow: Counter = Counter()
        self.slowest: dict[str, float] = {}
        self.unexplained: Counter = Counter()
        self.lock = threading.Lock()

    def record(self, worker: str, op: str, outcome: str) -> None:
        with self.lock:
            self.ops.append((round(time.monotonic() - self.t0, 3), worker, op, outcome))
            self.counts[f"{worker}:{op}:{outcome.split(':', 1)[0]}"] += 1

    def attempt(self, worker: str, op: str, fn, *args, **kwargs):
        from gitdirector.integrations.tmux import TmuxError, explain_tmux_failure

        started = time.monotonic()
        try:
            value = fn(*args, **kwargs)
        except (TmuxError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            message = str(exc).splitlines()[0][:160] if str(exc) else type(exc).__name__
            # A resource failure must reach the user as an explanation, not raw tmux.
            if any(marker in explain_tmux_failure(exc) for marker in RESOURCE_MARKERS):
                with self.lock:
                    self.unexplained[_normalize(message)] += 1
            if isinstance(exc, subprocess.CalledProcessError):
                message = f"{exc.cmd[:3]} -> {str(exc.stderr).strip()[:120]}"
            with self.lock:
                self.errors[f"{op}: {type(exc).__name__}: {_normalize(message)}"] += 1
            self.record(worker, op, f"error: {message}")
            return None
        except Exception as exc:  # noqa: BLE001 - every surprise is a finding
            key = f"{op}: {type(exc).__name__}: {str(exc)[:120]}"
            with self.lock:
                self.unexpected.setdefault(key, traceback.format_exc())
                self.errors[key] += 1
            self.record(worker, op, f"UNEXPECTED: {exc!r}")
            return None
        finally:
            elapsed = time.monotonic() - started
            with self.lock:
                self.slowest[op] = max(self.slowest.get(op, 0.0), round(elapsed, 2))
                if elapsed > 2.0:
                    self.slow[op] += 1
        self.record(worker, op, "ok")
        return value


def _normalize(message: str) -> str:
    import re

    message = re.sub(r"%\d+", "%N", message)
    message = re.sub(r"gd/[^\s:'\"]+", "gd/…", message)
    message = re.sub(r"\d{3,}", "N", message)
    return message


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True, choices=["normal", "ptystarve", "nproc", "chaos"])
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--out", type=Path, default=Path("/out"))
    parser.add_argument("--seed", type=int, default=None)
    opts = parser.parse_args()

    seed = opts.seed if opts.seed is not None else random.randrange(1 << 30)
    random.seed(seed)
    base = Path(tempfile.mkdtemp(prefix=f"gds-{opts.phase}-", dir="/tmp"))
    home = base / "home"
    home.mkdir()
    os.environ["HOME"] = str(home)
    os.environ["TMUX_TMPDIR"] = str(base)
    os.environ.pop("TMUX", None)
    repos = []
    for name in ("alpha", "beta", "gamma", "delta", "Space Repo"):
        repo = home / "repos" / name
        (repo / ".git").mkdir(parents=True)
        repos.append(repo)

    run = Run(opts.phase, opts.seconds, opts.out)
    run.t0 = time.monotonic()
    pts_baseline = pts_in_use()
    procs_baseline = _own_processes()

    nproc_limit = None
    preexec = None
    if opts.phase == "nproc":
        nproc_limit = len(_uid_processes()) + 45
        _, hard = resource.getrlimit(resource.RLIMIT_NPROC)
        preexec = lambda: resource.setrlimit(resource.RLIMIT_NPROC, (nproc_limit, hard))  # noqa: E731
    opts.out.mkdir(parents=True, exist_ok=True)
    # -v leaves the server's own log (its last words, if it dies) next to the report.
    verbose = ["-v"] if os.environ.get("STRESS_TMUX_LOG") == "1" else []
    started = subprocess.run(
        [
            "tmux",
            *verbose,
            "new-session",
            "-d",
            "-s",
            KEEPALIVE,
            "-x",
            "200",
            "-y",
            "50",
            "sleep 2147483647",
        ],
        capture_output=True,
        text=True,
        preexec_fn=preexec,
        cwd=opts.out,
    )
    if started.returncode != 0:
        raise SystemExit(f"tmux server did not start: {started.stderr}")
    server_pid = tmux("display-message", "-p", "-t", f"={KEEPALIVE}:", "#{pid}").stdout.strip()
    version = sh("tmux", "-V").stdout.strip()

    import gitdirector.integrations.tmux as T
    from gitdirector.commands.tui.panels import get_create_panel_layouts
    from gitdirector.integrations.tmux import core as C
    from gitdirector.integrations.tmux import deck as D

    layouts = get_create_panel_layouts()
    has_shell_kw = "shell" in T.create_tmux_session.__code__.co_varnames

    pts_max_before = None
    if opts.phase == "ptystarve":
        pts_max_before = 4096
        # Seed a few sessions first, then leave only a handful of ptys spare.
        for repo in repos[:3]:
            run.attempt("setup", "create", T.create_tmux_session, repo.name, repo)
        set_pts_max(pts_in_use() + 6)

    def gd_sessions() -> list[str]:
        return [e["session_name"] for e in T.list_all_gd_sessions()]

    # -- workers -------------------------------------------------------------

    def sessions_worker(name: str) -> None:
        while not run.stop.is_set():
            op = random.random()
            live = gd_sessions()
            if op < 0.45 or not live:
                repo = random.choice(repos)
                run.attempt(name, "create", T.create_tmux_session, repo.name, repo)
            elif op < 0.65:
                run.attempt(name, "kill", T.kill_tmux_session, random.choice(live))
            elif op < 0.75:
                run.attempt(
                    name, "send", T.send_text_to_session, random.choice(live), "echo hi", enter=True
                )
            elif op < 0.88:
                description = random.choice(["", "fix the login flow", "wip: räksmörgås ✓"])
                run.attempt(
                    name, "describe", C._set_session_description, random.choice(live), description
                )
                monitor.invalidate()
            else:
                run.attempt(name, "capture", T.capture_pane, random.choice(live), lines=20)
            time.sleep(random.uniform(0, 0.15))

    def agents_worker(name: str) -> None:
        while not run.stop.is_set():
            repo = random.choice(repos)
            kwargs = {"purpose": random.choice(["claude", "codex", "opencode"])}
            if has_shell_kw:
                kwargs["shell"] = False
            session = run.attempt(
                name, "create-agent", T.create_tmux_session, repo.name, repo, **kwargs
            )
            if session:
                command = random.choice(["sleep 0.3", "sleep 2", "true", "exit 3", "sleep 6"])
                marker = run.attempt(
                    name, "launch", T.launch_command_in_tmux_session, session, command
                )
                if marker is not None:
                    Path(marker).unlink(missing_ok=True)
            time.sleep(random.uniform(0.05, 0.3))

    def panels_worker(name: str) -> None:
        while not run.stop.is_set():
            live = gd_sessions()
            layout = random.choice(layouts)
            panes = {
                slot: (random.choice(live) if live and random.random() < 0.8 else None)
                for slot in range(1, layout.total_panes + 1)
            }
            panel = f"stress {random.randrange(3)}"
            if random.random() < 0.15:
                run.attempt(name, "panel-kill", T.kill_panel_tmux_session, panel)
            else:
                run.attempt(
                    name,
                    "panel-rebuild",
                    T.rebuild_panel_tmux_session,
                    panel,
                    layout.rows,
                    layout.cols,
                    panes,
                    closed_panes={s for s in panes if random.random() < 0.1},
                    layout_key=layout.key,
                )
            time.sleep(random.uniform(0.1, 0.5))

    def decks_worker(name: str) -> None:
        clients: list[subprocess.Popen] = []
        while not run.stop.is_set():
            live = gd_sessions()
            if not live:
                time.sleep(0.2)
                continue
            deck = run.attempt(name, "deck-create", D.create_deck, random.choice(live))
            if not deck:
                time.sleep(0.2)
                continue
            if random.random() < 0.5:
                # A real client on the deck: switch-client, views, destroy-unattached.
                clients.append(
                    subprocess.Popen(
                        ["script", "-qfec", f"tmux attach -t ={deck}", "/dev/null"],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                )
            for _ in range(random.randrange(1, 6)):
                if run.stop.is_set():
                    break
                live = gd_sessions()
                state = run.attempt(name, "deck-state", D.read_deck_state, deck)
                if state is None:
                    break
                choice = random.random()
                if choice < 0.6 and live:
                    run.attempt(name, "deck-show", D.show_session, deck, random.choice(live))
                elif choice < 0.8 and state.main is not None:
                    run.attempt(
                        name,
                        "deck-placeholder",
                        D.show_placeholder,
                        deck,
                        state.main.pane_id,
                        "t",
                        "d",
                        "h",
                    )
                elif state.main is not None:
                    # Close the main pane; the sidebar has to put it back.
                    run.attempt(
                        name, "deck-kill-main", _tmux_ok, "kill-pane", "-t", state.main.pane_id
                    )
                time.sleep(random.uniform(0.1, 0.6))
            if random.random() < 0.8:
                run.attempt(name, "deck-close", D.close_deck, deck)
            for client in clients[:-1]:
                _end(client)
            clients[:] = clients[-1:]
        for client in clients:
            _end(client)

    def monitor_worker(name: str) -> None:
        """What the console does: invalidate after a change, read, sample itself."""
        while not run.stop.is_set():
            monitor.invalidate()
            if monitor.entries() is not None:
                with run.lock:
                    run.unexpected.setdefault(
                        "monitor: entries() served a sample older than invalidate()", ""
                    )
            run.attempt(name, "monitor-refresh", monitor.refresh)
            run.attempt(name, "monitor-entries", monitor.entries)
            time.sleep(random.uniform(0.05, 0.4))

    def chaos_worker(name: str) -> None:
        """Pull the rug: kill pane programs, respawn and resize under the workers."""
        while not run.stop.is_set():
            panes = tmux("list-panes", "-a", "-F", "#{pane_id} #{pane_pid} #{session_name}")
            rows = [
                line.split(" ", 2)
                for line in panes.stdout.splitlines()
                if " gd/" in line and not line.split(" ", 2)[2].startswith(KEEPALIVE)
            ]
            if rows:
                pane_id, pid, session = random.choice(rows)
                choice = random.random()
                if choice < 0.35 and pid.isdigit():
                    run.attempt(name, f"kill-program {_comm(pid)}", _kill, int(pid))
                elif choice < 0.6:
                    run.attempt(name, "respawn", C.respawn_pane, pane_id)
                elif choice < 0.8:
                    size = f"{random.randrange(20, 240)}x{random.randrange(5, 70)}"
                    width, height = size.split("x")
                    run.attempt(
                        name,
                        "resize",
                        _tmux_ok,
                        "resize-window",
                        "-t",
                        session,
                        "-x",
                        width,
                        "-y",
                        height,
                    )
                elif choice < 0.9:
                    # What a user does: leave the shell, maybe while decks show it.
                    run.attempt(name, "exit", _tmux_ok, "send-keys", "-t", pane_id, "exit", "Enter")
                else:
                    run.attempt(name, "kill-session", T.kill_tmux_session, session)
            clients = tmux("list-clients", "-F", "#{client_pid}").stdout.split()
            if clients and random.random() < 0.3:
                client = random.choice(clients)
                run.attempt(name, f"kill-client {_comm(client)}", _kill, int(client))
            time.sleep(random.uniform(0.05, 0.3))

    def background_worker(name: str) -> None:
        while not run.stop.is_set():
            run.attempt(name, "sync-config", T.sync_panel_tmux_config)
            run.attempt(name, "list", T.list_all_gd_sessions)
            run.attempt(name, "reap-decks", D.reap_stale_decks)
            time.sleep(0.5)

    def hog_worker(name: str) -> None:
        held: list = []
        while not run.stop.is_set():
            target = random.randrange(0, 5)
            while len(held) > target:
                item = held.pop()
                if isinstance(item, subprocess.Popen):
                    _end(item)
                else:
                    for fd in item:
                        os.close(fd)
            while len(held) < target:
                try:
                    if opts.phase == "ptystarve":
                        held.append(os.openpty())
                    else:
                        held.append(subprocess.Popen(["sleep", "30"], preexec_fn=preexec))
                except OSError:
                    break
            time.sleep(random.uniform(0.1, 0.6))
        for item in held:
            if isinstance(item, subprocess.Popen):
                _end(item)
            else:
                for fd in item:
                    os.close(fd)

    def watchdog() -> None:
        while not run.stop.is_set():
            result = tmux("display-message", "-p", "-t", f"={KEEPALIVE}:", "#{pid}")
            if result.returncode != 0 or result.stdout.strip() != server_pid:
                again = tmux("display-message", "-p", "-t", f"={KEEPALIVE}:", "#{pid}")
                if again.returncode != 0 or again.stdout.strip() != server_pid:
                    with run.lock:
                        run.crashed = {
                            "at": round(time.monotonic() - run.t0, 3),
                            "stderr": (result.stderr or again.stderr).strip(),
                            # Server gone, or only the keepalive session?
                            "server_process_alive": Path(f"/proc/{server_pid}").exists(),
                            "server_process_state": _proc_state(server_pid),
                            "sessions_left": tmux(
                                "list-sessions", "-F", "#{session_name}"
                            ).stdout.split(),
                            "last_ops": list(run.ops)[-60:],
                        }
                    run.stop.set()
                    return
            panes = tmux("list-panes", "-a", "-F", "#{pane_id} #{pane_pid} #{session_name}")
            for line in panes.stdout.splitlines():
                pane_id, pid, session = line.split(" ", 2)
                if pid == "-1":
                    run.broken_panes_seen.add(f"{pane_id} {session}")
            time.sleep(0.1)

    monitor = T.TmuxMonitor()
    monitor.start()
    workers = [
        ("sessions-1", sessions_worker),
        ("sessions-2", sessions_worker),
        ("agents", agents_worker),
        ("panels-1", panels_worker),
        ("panels-2", panels_worker),
        ("decks-1", decks_worker),
        ("decks-2", decks_worker),
        ("background", background_worker),
        ("monitor", monitor_worker),
        ("watchdog", lambda _name: watchdog()),
    ]
    if opts.phase == "chaos":
        workers.append(("chaos", chaos_worker))
    if opts.phase in ("ptystarve", "nproc"):
        workers.append(("hog", hog_worker))
    threads = [
        threading.Thread(target=fn, args=(name,), name=name, daemon=True) for name, fn in workers
    ]
    for thread in threads:
        thread.start()
    run.stop.wait(opts.seconds)
    run.stop.set()
    stop_at = time.monotonic()
    import faulthandler

    for thread in threads:
        thread.join(timeout=5)
    stuck = [t.name for t in threads if t.is_alive()]
    if stuck:
        with open(opts.out / f"{opts.phase}-stacks.txt", "w") as handle:
            faulthandler.dump_traceback(file=handle, all_threads=True)
        for thread in threads:
            thread.join(timeout=60)
    shutdown_secs = round(time.monotonic() - stop_at, 1)
    monitor.stop()
    if pts_max_before is not None:
        set_pts_max(pts_max_before)

    # -- teardown checks -------------------------------------------------------
    report: dict = {
        "phase": opts.phase,
        "seed": seed,
        "tmux": version,
        "seconds": opts.seconds,
        "server_crashed": run.crashed,
        "ops": sum(run.counts.values()),
        "op_counts": dict(sorted(run.counts.items())),
        "errors": dict(run.errors.most_common(40)),
        "unexpected_tracebacks": run.unexpected,
        "unexplained_resource_errors": dict(run.unexplained),
        "broken_panes_seen_live": sorted(run.broken_panes_seen),
        "nproc_limit": nproc_limit,
        "slow_ops_over_2s": dict(run.slow),
        "slowest_op_secs": dict(sorted(run.slowest.items(), key=lambda kv: -kv[1])),
        "threads_stuck_after_stop": stuck,
        "shutdown_secs": shutdown_secs,
    }
    if run.crashed is None:
        time.sleep(1.0)
        broken_now = [
            line
            for line in tmux(
                "list-panes", "-a", "-F", "#{pane_pid} #{pane_id} #{session_name}"
            ).stdout.splitlines()
            if line.startswith("-1 ")
        ]
        report["broken_panes_at_end"] = broken_now
        killed = T.kill_all_gd_sessions()
        time.sleep(2.0)
        report["killed_at_end"] = len(killed)
        report["sessions_left_after_cleanup"] = [
            s
            for s in tmux("list-sessions", "-F", "#{session_name}").stdout.split()
            if s != KEEPALIVE
        ]
        report["server_pid_unchanged"] = (
            tmux("display-message", "-p", "-t", f"={KEEPALIVE}:", "#{pid}").stdout.strip()
            == server_pid
        )
    tmux("kill-server")
    time.sleep(2.0)
    leftovers = [p for p in _own_processes() if p["pid"] not in {q["pid"] for q in procs_baseline}]
    report["processes_left_after_kill_server"] = [
        f"{p['pid']} {p['ppid']} {p['tty']} {p['args']}" for p in leftovers
    ]
    report["pts_baseline"] = pts_baseline
    report["pts_after"] = pts_in_use()
    log = home / ".gitdirector" / "sidebar.log"
    sidebar_log = log.read_text().splitlines() if log.exists() else []
    report["sidebar_log_warnings"] = Counter(
        _normalize(line.split(": ", 1)[-1])[:160]
        for line in sidebar_log
        if " WARNING " in line or " ERROR " in line
    ).most_common(20)
    ok = (
        run.crashed is None
        and not run.unexpected
        and not run.unexplained
        and not report.get("broken_panes_at_end")
        and not report.get("sessions_left_after_cleanup")
        and report.get("server_pid_unchanged")
        and not leftovers
        and report["pts_after"] <= pts_baseline
    )
    report["verdict"] = "PASS" if ok else "FAIL"
    opts.out.mkdir(parents=True, exist_ok=True)
    path = opts.out / f"{opts.phase}.json"
    path.write_text(json.dumps(report, indent=2, default=str))
    summary = {k: report[k] for k in ("phase", "verdict", "ops") if k in report}
    summary["crashed"] = bool(run.crashed)
    summary["unexpected"] = len(run.unexpected)
    summary["leftover_procs"] = len(leftovers)
    summary["pts"] = f"{pts_baseline}->{report['pts_after']}"
    print(json.dumps(summary))
    return 0


def _tmux_ok(*args: str) -> None:
    from gitdirector.integrations.tmux import TmuxError

    result = tmux(*args)
    if result.returncode != 0:
        raise TmuxError(result.stderr.strip() or "tmux failed")


def _comm(pid: str) -> str:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()[:60].strip()
    except OSError:
        return "?"


def _proc_state(pid: str) -> str:
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(")")[-1].split()[0]
    except (OSError, IndexError):
        return "gone"


def _kill(pid: int) -> None:
    """SIGKILL *pid*; one that already exited is not a finding."""
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _end(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()


def _uid_processes() -> list[str]:
    return sh("ps", "-u", str(os.getuid()), "-o", "pid=").stdout.split()


def _own_processes() -> list[dict]:
    """Processes of this user outside this runner's own ancestry."""
    rows = sh("ps", "-u", str(os.getuid()), "-o", "pid=,ppid=,tty=,args=").stdout.splitlines()
    procs = []
    ancestors = set()
    pid = os.getpid()
    parents = {}
    for row in rows:
        parts = row.split(None, 3)
        if len(parts) >= 3:
            parents[int(parts[0])] = int(parts[1])
    while pid in parents:
        ancestors.add(pid)
        pid = parents[pid]
    for row in rows:
        parts = row.split(None, 3)
        if len(parts) < 4:
            continue
        p = {"pid": int(parts[0]), "ppid": int(parts[1]), "tty": parts[2], "args": parts[3]}
        if p["pid"] in ancestors or p["args"].startswith("ps "):
            continue
        procs.append(p)
    return procs


if __name__ == "__main__":
    sys.exit(main())
