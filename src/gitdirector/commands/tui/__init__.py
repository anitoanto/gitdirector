"""Interactive TUI console for GitDirector using Textual."""

from .app import GitDirectorConsole, register
from .constants import (
    _DEFAULT_PANELS_SORT_COLUMN,
    _SESSION_STATUS_LABEL,
    _SESSION_STATUS_ORDER,
    _PANELS_SORT_COLUMN_NAMES,
    _SESSIONS_SORT_COLUMN_NAMES,
    _SORT_COLUMN_NAMES,
    _STATUS_LABEL,
    _STATUS_ORDER,
    _changes_label,
)
from .panel_view import PanelViewScreen, PaneWidget
from .panels import Panel, PanelStore
from .terminal_widget import TerminalWidget
from .screens import (
    ActionMenuScreen,
    AgentLoadingScreen,
    ConfirmScreen,
    CreatePanelScreen,
    RemoveSessionScreen,
    RepoInfoScreen,
    SelectSessionScreen,
    SortMenuScreen,
)

__all__ = [
    "ActionMenuScreen",
    "AgentLoadingScreen",
    "ConfirmScreen",
    "CreatePanelScreen",
    "_DEFAULT_PANELS_SORT_COLUMN",
    "GitDirectorConsole",
    "PaneWidget",
    "Panel",
    "PanelStore",
    "PanelViewScreen",
    "_PANELS_SORT_COLUMN_NAMES",
    "RemoveSessionScreen",
    "RepoInfoScreen",
    "SelectSessionScreen",
    "SortMenuScreen",
    "TerminalWidget",
    "_SESSION_STATUS_LABEL",
    "_SESSION_STATUS_ORDER",
    "_SESSIONS_SORT_COLUMN_NAMES",
    "_SORT_COLUMN_NAMES",
    "_STATUS_LABEL",
    "_STATUS_ORDER",
    "_changes_label",
    "register",
]
