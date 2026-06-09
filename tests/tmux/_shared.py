import fcntl
import shutil
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path

from gitdirector.integrations.tmux import TmuxMonitor

REAL_TMUX_MONITOR_START = TmuxMonitor.start
REAL_TMUX_MONITOR_STOP = TmuxMonitor.stop


@contextmanager
def _tmux_integration_lock():
    lock_path = Path(tempfile.gettempdir()) / "gitdirector-tmux-integration.lock"
    with lock_path.open("w") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _make_short_tmux_tmpdir(prefix: str = "gd-tmux-") -> Path:
    """Create a tmux socket directory under ``/tmp`` (or the system short tmp).

    tmux's default socket name ``tmux-<uid>/default`` consumes about 17 bytes
    of the OS-specific unix socket path limit (104 on macOS, 108 on Linux).
    If ``TMUX_TMPDIR`` is too deep, tmux refuses to start with
    ``error connecting to ... (File name too long)``.

    Using ``/tmp`` directly keeps the socket path comfortably under the limit
    on every supported platform and test environment.
    """
    base = Path("/tmp")
    if not base.exists():
        base = Path(tempfile.gettempdir())
    suffix = uuid.uuid4().hex[:8]
    tmux_dir = base / f"{prefix}{suffix}"
    tmux_dir.mkdir(parents=True, exist_ok=False)
    return tmux_dir


def _cleanup_tmux_tmpdir(tmux_dir: Path) -> None:
    shutil.rmtree(tmux_dir, ignore_errors=True)
