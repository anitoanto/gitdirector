"""Repository group detection and table helpers for the TUI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from rich.markup import escape
from textual.css.query import NoMatches
from textual.widgets import DataTable, Static

from .constants import _DEFAULT_GROUPS_SORT_COLUMN, _GROUPS_SORT_COLUMN_NAMES
from .table_text import resolve_wrapped_column_width, wrap_table_cell_text

_MIN_GROUP_REPOSITORIES_WIDTH = 24
_MAX_GROUP_REPOSITORIES_WIDTH = 96
_GROUP_REPOSITORIES_WIDTH_DIVISOR = 2
_GROUPS_COL_REPOSITORIES = 1


@dataclass
class RepoGroup:
    path: Path
    repositories: tuple[Path, ...]

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def repo_count(self) -> int:
        return len(self.repositories)

    @property
    def repo_names(self) -> str:
        return ", ".join(path.name for path in self.repositories)


def detect_repo_groups(repo_paths: Iterable[Path]) -> list[RepoGroup]:
    parents: dict[Path, set[Path]] = {}
    for repo_path in repo_paths:
        parents.setdefault(repo_path.parent, set()).add(repo_path)

    groups = [
        RepoGroup(path=parent, repositories=tuple(sorted(repos, key=lambda p: p.name.lower())))
        for parent, repos in parents.items()
        if len(repos) >= 2
    ]
    groups.sort(key=lambda group: (group.name.lower(), str(group.path).lower()))
    return groups


def group_session_repo_label(path: Path) -> str:
    from ...integrations.tmux import _sanitize_repo_name

    return f"group_{_sanitize_repo_name(path.name) or 'repo'}"


class ConsoleGroupsMixin:
    def _resolve_group_repositories_width(self) -> int:
        return resolve_wrapped_column_width(
            self.size.width,
            min_width=_MIN_GROUP_REPOSITORIES_WIDTH,
            max_width=_MAX_GROUP_REPOSITORIES_WIDTH,
            divisor=_GROUP_REPOSITORIES_WIDTH_DIVISOR,
        )

    def _apply_groups_repositories_column_width(self) -> None:
        try:
            table = self.query_one("#groups-table", DataTable)
        except NoMatches:
            return
        col_keys = getattr(self, "_groups_col_keys", None)
        if not col_keys or len(col_keys) <= _GROUPS_COL_REPOSITORIES:
            return
        try:
            column = table.columns[col_keys[_GROUPS_COL_REPOSITORIES]]
        except (KeyError, IndexError):
            return
        column.auto_width = False
        column.width = self._resolve_group_repositories_width()

    def _load_groups(self) -> None:
        try:
            table = self.query_one("#groups-table", DataTable)
        except NoMatches:
            return

        repo_paths = sorted(
            self.manager.config.repositories,
            key=lambda path: (str(path.parent).lower(), path.name.lower()),
        )
        self._groups_entries = detect_repo_groups(repo_paths)
        self._apply_groups_repositories_column_width()
        self._apply_groups_filter_and_sort()
        table.focus()

    def _group_sort_key_func(self):
        col = self._groups_sort_column
        if col == 1:
            return lambda group: (group.repo_count, group.name.lower())
        if col == 2:
            return lambda group: str(group.path).lower()
        return lambda group: group.name.lower()

    def _group_matches_search(self, group: RepoGroup, query: str) -> bool:
        haystacks = [
            group.name.lower(),
            str(group.path).lower(),
            group.repo_names.lower(),
        ]
        return any(query in haystack for haystack in haystacks)

    def _apply_groups_filter_and_sort(self) -> None:
        try:
            table = self.query_one("#groups-table", DataTable)
        except NoMatches:
            return
        self._apply_groups_repositories_column_width()
        repo_names_width = self._resolve_group_repositories_width()
        preserved_row_key = None
        preserved_row_index = None
        restore_focus = False
        if self._resume_selection_tab != "groups":
            preserved_row_key, preserved_row_index, restore_focus = self._capture_table_selection(
                table
            )
        table.clear()
        no_msg = self.query_one("#no-groups-message", Static)

        groups = list(self._groups_entries)
        total = len(groups)

        if self._search_query:
            query = self._search_query.lower()
            groups = [group for group in groups if self._group_matches_search(group, query)]

        groups.sort(key=self._group_sort_key_func(), reverse=self._groups_sort_reverse)

        if not groups and total == 0 and not self._search_query:
            table.display = False
            no_msg.display = True
        else:
            table.display = True
            no_msg.display = False
            for group in groups:
                repo_names = f"{group.repo_count}: {group.repo_names}"
                wrapped_repo_names = wrap_table_cell_text(repo_names, repo_names_width)
                table.add_row(
                    group.name,
                    wrapped_repo_names,
                    str(group.path),
                    height=max(1, len(wrapped_repo_names.splitlines())),
                    key=str(group.path),
                )

        if self._resume_selection_tab == "groups":
            self._restore_resume_selection("groups")
        else:
            self._restore_table_selection(
                table,
                preserved_row_key,
                preserved_row_index,
                restore_focus=restore_focus,
            )
        self._update_status(self._build_groups_loaded_status(len(groups), total))

    def _build_groups_loaded_status(self, shown: int, total: int) -> str:
        if total == 0 and not self._search_query:
            return "No groups detected   4 groups  1 repos  2 sessions  3 panels  q quit"

        if self._search_query:
            count_str = f"{shown} of {total}"
        else:
            count_str = str(total)

        label_count = shown if self._search_query else total
        label = "group" if label_count == 1 else "groups"
        msg = f"{count_str} {label}"

        indicators: list[str] = []
        if self._search_query:
            indicators.append(f"filter: '{escape(self._search_query)}'")
        if self._groups_sort_column != _DEFAULT_GROUPS_SORT_COLUMN or self._groups_sort_reverse:
            direction = "▼" if self._groups_sort_reverse else "▲"
            indicators.append(
                f"sort: {_GROUPS_SORT_COLUMN_NAMES[self._groups_sort_column]} {direction}"
            )
        if indicators:
            msg += f"  ({', '.join(indicators)})"

        msg += "   ↑↓/jk navigate  [enter] actions  / search  s sort  r refresh"
        msg += "  1 repos  2 sessions  3 panels  q quit"
        if self._search_query:
            msg += "  [esc] clear search"
        return msg

    def _get_selected_group(self) -> RepoGroup | None:
        table = self.query_one("#groups-table", DataTable)
        row_key = self._get_selected_row_key(table)
        if row_key is None:
            return None
        for group in self._groups_entries:
            if str(group.path) == row_key:
                return group
        return None

    def _get_selected_group_session_repo_label(self) -> str | None:
        group = self._get_selected_group()
        if group is None:
            return None
        return group_session_repo_label(group.path)

    def _handle_groups_sort_selection(self, result: tuple | None) -> None:
        if result is None:
            return
        self._groups_sort_column, self._groups_sort_reverse = result
        self._apply_groups_filter_and_sort()
