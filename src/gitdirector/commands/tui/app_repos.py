"""Repository list loading and filtering helpers for the TUI."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from rich.markup import escape
from textual import work
from textual.widgets import DataTable, Static

from ...repo import RepositoryInfo, RepoStatus
from .app_groups import RepoGroup, detect_repo_groups
from .constants import (
    _DEFAULT_SORT_COLUMN,
    _SORT_COLUMN_NAMES,
    _STATUS_LABEL,
    _STATUS_ORDER,
    _changes_label,
)

logger = logging.getLogger(__name__)


class ConsoleReposMixin:
    def _repo_path_sort_key(self, path: Path) -> tuple[str, str, str]:
        return (path.parent.name.lower(), str(path.parent).lower(), path.name.lower())

    @work(thread=True)
    def _load_repos(self) -> None:
        worker = self._current_worker_or_none()

        def shutdown_requested() -> bool:
            return self._background_shutdown_requested(worker)

        self._repo_paths = sorted(self.manager.config.repositories, key=self._repo_path_sort_key)
        self._groups_entries = detect_repo_groups(self._repo_paths)

        if not self._repo_paths:
            if not shutdown_requested():
                self.call_from_thread(self._show_no_repos)
            return

        if shutdown_requested():
            return

        self.call_from_thread(self._populate_initial_rows)

        total = len(self._repo_paths)
        done = 0
        self.call_from_thread(self._update_status, f"Checking {total} repositories…")

        executor = ThreadPoolExecutor(max_workers=self.manager.config.max_workers)
        self._repo_status_executor = executor
        try:
            futures = {
                executor.submit(self.manager.get_repository_status, path, fetch=True): path
                for path in self._repo_paths
            }
            for future in as_completed(futures):
                if shutdown_requested():
                    break
                path = futures[future]
                try:
                    info = future.result()
                except Exception as exc:
                    info = RepositoryInfo(path, path.name, RepoStatus.UNKNOWN, None, str(exc))
                if shutdown_requested():
                    break
                self._results[str(info.path)] = info
                done += 1
                self.call_from_thread(self._update_row, info)
                remaining = total - done
                if shutdown_requested():
                    break
                if remaining > 0:
                    self.call_from_thread(
                        self._update_status,
                        f"{done} done, {remaining} remaining…",
                    )
        finally:
            executor.shutdown(wait=not shutdown_requested(), cancel_futures=shutdown_requested())
            if self._repo_status_executor is executor:
                self._repo_status_executor = None

        if shutdown_requested():
            return

        if self._search_query or self._sort_column != _DEFAULT_SORT_COLUMN or self._sort_reverse:
            self.call_from_thread(self._apply_filter_and_sort)
        else:
            self.call_from_thread(
                self._update_status,
                self._build_loaded_status(total, total),
            )

    def _populate_initial_rows(self) -> None:
        table = self.query_one("#repo-table", DataTable)
        self.query_one("#no-repos-message", Static).display = False
        table.display = True
        preserved_row_key = None
        preserved_row_index = None
        restore_focus = False
        if self._resume_selection_tab != "repos":
            preserved_row_key, preserved_row_index, restore_focus = self._capture_table_selection(
                table
            )
        table.clear()
        self._render_repo_path_rows(table, self._repo_paths)

        if self._resume_selection_tab == "repos":
            self._restore_resume_selection("repos")
        else:
            self._restore_table_selection(
                table,
                preserved_row_key,
                preserved_row_index,
                restore_focus=restore_focus,
            )

    def _update_row(self, info: RepositoryInfo) -> None:
        table = self.query_one("#repo-table", DataTable)
        row_key = str(info.path)
        ck = self._col_keys
        try:
            table.update_cell(row_key, ck[1], _STATUS_LABEL.get(info.status, "unknown"))
            table.update_cell(row_key, ck[2], info.branch or "—")
            table.update_cell(row_key, ck[3], _changes_label(info))
            table.update_cell(row_key, ck[4], info.last_updated or "—")
        except Exception:
            logger.debug("Failed to update repo row %s", row_key, exc_info=True)

    def _show_no_repos(self) -> None:
        self.query_one("#repo-table", DataTable).display = False
        self.query_one("#no-repos-message", Static).display = True
        self._visible_repo_count = 0
        self._visible_group_count = 0
        self._update_status("No repositories linked")

    def _sort_key_func(self):
        col = self._sort_column
        if col == 1:
            return lambda info: _STATUS_ORDER.get(info.status, 99)
        if col == 2:
            return lambda info: (info.branch or "").lower()
        if col == 3:
            return lambda info: _changes_label(info)
        if col == 4:
            return lambda info: info.last_commit_timestamp or 0
        if col == 5:
            return lambda info: str(info.path).lower()
        return lambda info: info.name.lower()

    def _repo_matches_search(self, info: RepositoryInfo, query: str) -> bool:
        return (
            query in info.name.lower()
            or query in (info.branch or "").lower()
            or query in str(info.path).lower()
        )

    def _filter_repo_infos_for_search(
        self,
        infos: list[RepositoryInfo],
    ) -> list[RepositoryInfo]:
        if not self._search_query:
            return list(infos)

        query = self._search_query.lower()
        infos_by_path = {info.path: info for info in infos}
        included_paths: set[Path] = set()

        for group in self._groups_entries:
            group_infos = [
                infos_by_path[path] for path in group.repositories if path in infos_by_path
            ]
            if not group_infos:
                continue
            if self._group_matches_search(group, query):
                included_paths.update(info.path for info in group_infos)
            else:
                included_paths.update(
                    info.path for info in group_infos if self._repo_matches_search(info, query)
                )

        for info in infos:
            if info.path not in included_paths and self._repo_matches_search(info, query):
                included_paths.add(info.path)

        return [info for info in infos if info.path in included_paths]

    def _repo_group_is_collapsed(self, group: RepoGroup) -> bool:
        return not self._search_query and str(group.path) in self._collapsed_groups

    def _repo_group_label(self, group: RepoGroup) -> str:
        marker = "▸" if self._repo_group_is_collapsed(group) else "▾"
        return f"[bold cyan]{marker} {escape(group.name)}[/bold cyan]"

    def _repo_group_count_label(self, shown: int, total: int) -> str:
        repo_label = "repo" if total == 1 else "repos"
        if shown == total:
            return f"{total} {repo_label}"
        return f"{shown}/{total} {repo_label}"

    def _add_repo_group_row(self, table: DataTable, group: RepoGroup, shown: int) -> None:
        table.add_row(
            self._repo_group_label(group),
            f"[bold]{self._repo_group_count_label(shown, group.repo_count)}[/bold]",
            "[dim]group[/dim]",
            "[dim]enter actions[/dim]",
            "—",
            str(group.path),
            key=self._group_row_key(group.path),
        )

    def _add_placeholder_repo_row(self, table: DataTable, path: Path, *, grouped: bool) -> None:
        table.add_row(
            f"  {path.name}" if grouped else path.name,
            "... ... ... ...",
            "... ... ... ...",
            "... ... ... ...",
            "... ... ... ... ... ...",
            str(path),
            key=str(path),
        )

    def _add_repo_info_row(
        self,
        table: DataTable,
        info: RepositoryInfo,
        *,
        grouped: bool,
    ) -> None:
        table.add_row(
            f"  {info.name}" if grouped else info.name,
            _STATUS_LABEL.get(info.status, "unknown"),
            info.branch or "—",
            _changes_label(info),
            info.last_updated or "—",
            str(info.path),
            key=str(info.path),
        )

    def _add_repo_path_row(self, table: DataTable, path: Path, *, grouped: bool) -> None:
        info = self._results.get(str(path))
        if info is None:
            self._add_placeholder_repo_row(table, path, grouped=grouped)
            return
        self._add_repo_info_row(table, info, grouped=grouped)

    def _render_repo_path_rows(self, table: DataTable, paths: list[Path]) -> None:
        path_set = set(paths)
        grouped_paths: set[Path] = set()
        shown_repo_count = 0
        shown_group_count = 0

        for group in self._groups_entries:
            group_paths = [path for path in group.repositories if path in path_set]
            if not group_paths:
                continue
            shown_group_count += 1
            shown_repo_count += len(group_paths)
            grouped_paths.update(group_paths)
            self._add_repo_group_row(table, group, len(group_paths))
            if self._repo_group_is_collapsed(group):
                continue
            for path in sorted(group_paths, key=lambda item: item.name.lower()):
                self._add_repo_path_row(table, path, grouped=True)

        ungrouped_paths = [path for path in paths if path not in grouped_paths]
        shown_repo_count += len(ungrouped_paths)
        for path in sorted(ungrouped_paths, key=lambda item: item.name.lower()):
            self._add_repo_path_row(table, path, grouped=False)

        self._visible_repo_count = shown_repo_count
        self._visible_group_count = shown_group_count

    def _render_repo_info_rows(
        self,
        table: DataTable,
        infos: list[RepositoryInfo],
    ) -> None:
        infos_by_path = {info.path: info for info in infos}
        grouped_paths: set[Path] = set()
        shown_repo_count = 0
        shown_group_count = 0
        key_func = self._sort_key_func()

        for group in self._groups_entries:
            group_infos = [
                infos_by_path[path] for path in group.repositories if path in infos_by_path
            ]
            if not group_infos:
                continue
            shown_group_count += 1
            shown_repo_count += len(group_infos)
            grouped_paths.update(info.path for info in group_infos)
            self._add_repo_group_row(table, group, len(group_infos))
            if self._repo_group_is_collapsed(group):
                continue
            group_infos.sort(key=key_func, reverse=self._sort_reverse)
            for info in group_infos:
                self._add_repo_info_row(table, info, grouped=True)

        ungrouped_infos = [info for info in infos if info.path not in grouped_paths]
        shown_repo_count += len(ungrouped_infos)
        ungrouped_infos.sort(key=key_func, reverse=self._sort_reverse)
        for info in ungrouped_infos:
            self._add_repo_info_row(table, info, grouped=False)

        self._visible_repo_count = shown_repo_count
        self._visible_group_count = shown_group_count

    def _apply_filter_and_sort(self) -> None:
        table = self.query_one("#repo-table", DataTable)
        self.query_one("#no-repos-message", Static).display = False
        table.display = True
        preserved_row_key = None
        preserved_row_index = None
        restore_focus = False
        if self._resume_selection_tab != "repos":
            preserved_row_key, preserved_row_index, restore_focus = self._capture_table_selection(
                table
            )
        table.clear()

        infos = list(self._results.values())
        total = len(infos)
        infos = self._filter_repo_infos_for_search(infos)
        self._render_repo_info_rows(table, infos)

        if self._resume_selection_tab == "repos":
            self._restore_resume_selection("repos")
        else:
            self._restore_table_selection(
                table,
                preserved_row_key,
                preserved_row_index,
                restore_focus=restore_focus,
            )
        self._update_status(self._build_loaded_status(len(infos), total))

    def _build_loaded_status(self, shown: int, total: int) -> str:
        if total == 0 and not self._search_query:
            return "No repositories tracked"

        if self._search_query:
            count_str = f"{shown} of {total}"
        else:
            count_str = str(total)

        label = "repository" if shown == 1 else "repositories"
        group_count = getattr(self, "_visible_group_count", 0)
        if group_count:
            group_label = "group" if group_count == 1 else "groups"
            msg = f"{count_str} {label} loaded in {group_count} {group_label}"
        else:
            msg = f"{count_str} {label} loaded"

        indicators: list[str] = []
        if self._search_query:
            indicators.append(f"filter: '{escape(self._search_query)}'")
        if self._sort_column != _DEFAULT_SORT_COLUMN or self._sort_reverse:
            direction = "▼" if self._sort_reverse else "▲"
            indicators.append(f"sort: {_SORT_COLUMN_NAMES[self._sort_column]} {direction}")
        if indicators:
            msg += f"  ({', '.join(indicators)})"

        msg += "   ↑↓/jk navigate  [enter] actions"
        if group_count:
            msg += "  [space] toggle group"
        msg += "  g git  / search  s sort  r refresh  q quit"
        if self._search_query:
            msg += "  [esc] clear search"
        if self._waiting_count > 0:
            waiting = self._waiting_count
            waiting_label = "session" if waiting == 1 else "sessions"
            msg += f"  ⟐ {waiting} {waiting_label} waiting"
        return msg

    def action_toggle_group(self) -> None:
        if self._active_tab != "repos":
            return
        group = self._get_selected_group()
        if group is None:
            return
        group_key = str(group.path)
        if group_key in self._collapsed_groups:
            self._collapsed_groups.remove(group_key)
        else:
            self._collapsed_groups.add(group_key)
        if self._search_query or len(self._results) >= len(self._repo_paths):
            self._apply_filter_and_sort()
        else:
            self._populate_initial_rows()

    def action_refresh(self) -> None:
        if self._active_tab == "sessions":
            self._load_sessions()
        elif self._active_tab == "panels":
            self._load_panels()
        else:
            self._results.clear()
            self._load_repos()
