"""Repository group detection and row helpers for the TUI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from textual.widgets import DataTable

_GROUP_ROW_PREFIX = "__gitdirector_group__:"


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
    from ...integrations.tmux.core import _sanitize_repo_name

    return f"group_{_sanitize_repo_name(path.name) or 'repo'}"


def group_row_key(path: Path) -> str:
    return f"{_GROUP_ROW_PREFIX}{path}"


def group_path_from_row_key(row_key: str) -> Path | None:
    if not row_key.startswith(_GROUP_ROW_PREFIX):
        return None
    return Path(row_key.removeprefix(_GROUP_ROW_PREFIX))


class ConsoleGroupsMixin:
    def _group_matches_search(self, group: RepoGroup, query: str) -> bool:
        haystacks = [
            group.name.lower(),
            str(group.path).lower(),
        ]
        return any(query in haystack for haystack in haystacks)

    def _group_row_key(self, path: Path) -> str:
        return group_row_key(path)

    def _group_path_from_row_key(self, row_key: str) -> Path | None:
        return group_path_from_row_key(row_key)

    def _row_key_is_group(self, row_key: str | None) -> bool:
        return row_key is not None and self._group_path_from_row_key(row_key) is not None

    def _selected_repo_row_key(self) -> str | None:
        try:
            table = self.query_one("#repo-table", DataTable)
        except Exception:
            return None
        return self._get_selected_row_key(table)

    def _selected_repo_row_is_group(self) -> bool:
        return self._row_key_is_group(self._selected_repo_row_key())

    def _repo_group_for_path(self, path: Path) -> RepoGroup | None:
        for group in self._groups_entries:
            if group.path == path:
                return group
        return None

    def _get_selected_group(self) -> RepoGroup | None:
        row_key = self._selected_repo_row_key()
        if row_key is None:
            return None
        group_path = self._group_path_from_row_key(row_key)
        return self._repo_group_for_path(group_path) if group_path is not None else None

    def _get_selected_group_session_repo_label(self) -> str | None:
        group = self._get_selected_group()
        if group is None:
            return None
        return group_session_repo_label(group.path)
