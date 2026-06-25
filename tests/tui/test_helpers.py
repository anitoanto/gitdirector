"""Tests for TUI helper functions and constants."""

from gitdirector.commands.tui import (
    _SORT_COLUMN_NAMES,
    _STATUS_LABEL,
    _STATUS_ORDER,
    _changes_label,
)
from gitdirector.repo import RepoStatus

from .conftest import _make_info


class TestChangesLabel:
    def test_staged_and_unstaged(self):
        info = _make_info(staged=True, unstaged=True)
        assert _changes_label(info) == "[bold yellow]staged+unstaged[/bold yellow]"

    def test_staged_only(self):
        info = _make_info(staged=True, unstaged=False)
        assert _changes_label(info) == "[bold yellow]staged[/bold yellow]"

    def test_unstaged_only(self):
        info = _make_info(staged=False, unstaged=True)
        assert _changes_label(info) == "[bold yellow]unstaged[/bold yellow]"

    def test_no_changes(self):
        info = _make_info(staged=False, unstaged=False)
        assert _changes_label(info) == "—"


class TestStatusLabel:
    def test_all_statuses_covered(self):
        for s in RepoStatus:
            assert s in _STATUS_LABEL

    def test_specific_values(self):
        assert _STATUS_LABEL[RepoStatus.UP_TO_DATE] == "up to date"
        assert _STATUS_LABEL[RepoStatus.BEHIND] == "[bold yellow]behind[/bold yellow]"
        assert _STATUS_LABEL[RepoStatus.AHEAD] == "[bold yellow]ahead[/bold yellow]"
        assert _STATUS_LABEL[RepoStatus.DIVERGED] == "[bold yellow]diverged[/bold yellow]"
        assert _STATUS_LABEL[RepoStatus.UNKNOWN] == "[bold yellow]unknown[/bold yellow]"


class TestSortConstants:
    def test_sort_column_names_count(self):
        assert len(_SORT_COLUMN_NAMES) == 6

    def test_status_order_covers_all(self):
        for s in RepoStatus:
            assert s in _STATUS_ORDER
