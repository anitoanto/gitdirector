from contextlib import contextmanager
import fcntl
import tempfile
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
