"""tmux integration: sessions, panels, and the session status monitor.

Only the public API is re-exported here. Internals live in :mod:`.core`,
:mod:`.monitor`, :mod:`.panels`, and :mod:`.session_env`.
"""

from .core import (
    TmuxError,
    attach_tmux_session,
    capture_pane,
    create_tmux_session,
    kill_all_gd_sessions,
    kill_tmux_session,
    list_all_gd_sessions,
    list_repo_sessions,
    open_in_tmux,
    send_key_to_session,
    send_text_to_session,
    sync_panel_tmux_config,
)
from .monitor import (
    STATUS_IDLE,
    STATUS_RUNNING,
    STATUS_WAITING,
    TmuxMonitor,
    launch_command_in_tmux_session,
    resolve_pane_status,
)
from .panels import (
    cleanup_panel_attached_session,
    cleanup_temp_panel_tmux_session,
    ensure_temp_panel_tmux_session,
    kill_panel_tmux_session,
    panel_tmux_session_exists,
    rebuild_panel_tmux_session,
)
from .session_env import SCRUB_POLICY_ENV_VAR, sanitized_environ, session_scrub_names

__all__ = [
    "SCRUB_POLICY_ENV_VAR",
    "STATUS_IDLE",
    "STATUS_RUNNING",
    "STATUS_WAITING",
    "TmuxError",
    "TmuxMonitor",
    "attach_tmux_session",
    "capture_pane",
    "cleanup_panel_attached_session",
    "cleanup_temp_panel_tmux_session",
    "create_tmux_session",
    "ensure_temp_panel_tmux_session",
    "kill_all_gd_sessions",
    "kill_panel_tmux_session",
    "kill_tmux_session",
    "launch_command_in_tmux_session",
    "list_all_gd_sessions",
    "list_repo_sessions",
    "open_in_tmux",
    "panel_tmux_session_exists",
    "rebuild_panel_tmux_session",
    "resolve_pane_status",
    "sanitized_environ",
    "send_key_to_session",
    "send_text_to_session",
    "session_scrub_names",
    "sync_panel_tmux_config",
]
