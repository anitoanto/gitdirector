"""Modal screen classes for the TUI, split by domain:

- ``screens.repos``   — repository action menu, git command results, pull, info
- ``screens.sessions`` — tmux session selection and removal
- ``screens.panels``  — panel create/reconfigure/rename/action and agent loading
- ``screens.commit``  — stage-all confirm, commit message, commit loading,
  commit result
- ``screens._shared`` — generic confirm and sort dialogs, plus the shared ANSI
  renderer

This package re-exports the same public surface that the previous monolithic
``screens`` module exposed, so existing imports keep working unchanged.
"""

from __future__ import annotations

from ._shared import ConfirmScreen, SortMenuScreen, _render_ansi_output
from .commit import (
    CommitLoadingScreen,
    CommitMessageScreen,
    CommitResultScreen,
    StageFilesConfirmScreen,
)
from .diff import DiffReviewScreen
from .panels import (
    AgentLoadingScreen,
    CreatePanelScreen,
    PanelActionMenuScreen,
    RenamePanelScreen,
    _render_grid_preview,
)
from .repos import (
    ActionMenuScreen,
    GitCommandResultScreen,
    GitOperationsMenuScreen,
    PullLoadingScreen,
    PullResultScreen,
    RepoInfoScreen,
)
from .sessions import RemoveSessionScreen, SelectSessionScreen

__all__ = [
    "ActionMenuScreen",
    "AgentLoadingScreen",
    "CommitLoadingScreen",
    "CommitMessageScreen",
    "CommitResultScreen",
    "ConfirmScreen",
    "CreatePanelScreen",
    "DiffReviewScreen",
    "GitCommandResultScreen",
    "GitOperationsMenuScreen",
    "PanelActionMenuScreen",
    "PullLoadingScreen",
    "PullResultScreen",
    "RemoveSessionScreen",
    "RenamePanelScreen",
    "RepoInfoScreen",
    "SelectSessionScreen",
    "SortMenuScreen",
    "StageFilesConfirmScreen",
    "_render_ansi_output",
    "_render_grid_preview",
]
