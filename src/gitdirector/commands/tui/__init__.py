"""Interactive TUI console for GitDirector using Textual."""

from .app import GitDirectorConsole, register
from .constants import (
    _DEFAULT_PANELS_SORT_COLUMN,
    _PANELS_SORT_COLUMN_NAMES,
    _SESSION_STATUS_LABEL,
    _SESSION_STATUS_ORDER,
    _SESSIONS_SORT_COLUMN_NAMES,
    _SORT_COLUMN_NAMES,
    _STATUS_LABEL,
    _STATUS_ORDER,
    _changes_label,
    _changes_sort_key,
)
from .panel_view import PanelViewScreen, PaneWidget
from .panels import Panel, PanelStore
from .screens import SortMenuScreen
from .screens.diff import DiffReviewScreen
from .screens.groups import GroupActionMenuScreen
from .screens.panels import (
    AgentLoadingScreen,
    ConfirmScreen,
    CreatePanelScreen,
    PanelActionMenuScreen,
    RenamePanelScreen,
)
from .screens.repos import (
    ActionMenuScreen,
    GitCommandResultScreen,
    GitOperationsMenuScreen,
    PullLoadingScreen,
    PullResultScreen,
    RepoInfoScreen,
)
from .screens.sessions import RemoveSessionScreen, SelectSessionScreen
from .terminal_widget import TerminalWidget

__all__ = [
    "ActionMenuScreen",
    "AgentLoadingScreen",
    "ConfirmScreen",
    "CreatePanelScreen",
    "DiffReviewScreen",
    "GitCommandResultScreen",
    "GitOperationsMenuScreen",
    "GroupActionMenuScreen",
    "_DEFAULT_PANELS_SORT_COLUMN",
    "GitDirectorConsole",
    "PaneWidget",
    "Panel",
    "PanelActionMenuScreen",
    "PanelStore",
    "PanelViewScreen",
    "_PANELS_SORT_COLUMN_NAMES",
    "PullLoadingScreen",
    "PullResultScreen",
    "RemoveSessionScreen",
    "RenamePanelScreen",
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
    "_changes_sort_key",
    "register",
]
