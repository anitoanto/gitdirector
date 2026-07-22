"""Repository list loading and filtering helpers for the TUI."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from time import monotonic, time

from rich.markup import escape
from textual import work
from textual.widgets import DataTable, Static

from ...repo import RepositoryInfo, RepoStatus
from ...storage import load_yaml_mapping, write_yaml_atomic
from .app_groups import RepoGroup, detect_repo_groups
from .constants import (
    _DEFAULT_SORT_COLUMN,
    _REPO_CACHE_TTL_SECS,
    _SORT_COLUMN_NAMES,
    _STATUS_LABEL,
    _STATUS_ORDER,
    _changes_label,
    _changes_sort_key,
)

logger = logging.getLogger(__name__)


class ConsoleReposMixin:
    @property
    def _repos_cache_file(self) -> Path:
        return Path.home() / ".gitdirector" / "cache" / "repos.yaml"

    def _save_repos_cache(self, *, updated_at: float | None = None) -> None:
        saved_at = time() if updated_at is None else updated_at
        data = {
            "updated_at": saved_at,
            "repositories": [
                {
                    "path": str(info.path),
                    "name": info.name,
                    "status": info.status.value,
                    "branch": info.branch,
                    "message": info.message,
                    "staged": info.staged,
                    "unstaged": info.unstaged,
                    "staged_files": info.staged_files,
                    "unstaged_files": info.unstaged_files,
                    "last_updated": info.last_updated,
                    "last_commit_timestamp": info.last_commit_timestamp,
                    "size": info.size,
                }
                for info in self._results.values()
            ],
        }
        try:
            write_yaml_atomic(self._repos_cache_file, data)
        except OSError:
            logger.debug("Failed to write repository cache", exc_info=True)
        else:
            self._repos_cache_saved_at = saved_at

    def _load_repos_from_cache(self) -> bool:
        try:
            data = load_yaml_mapping(self._repos_cache_file, description="repository cache")
            updated_at = data["updated_at"]
            entries = data["repositories"]
            if isinstance(updated_at, bool) or not isinstance(updated_at, (int, float)):
                return False
            if not isinstance(entries, list):
                return False
            age = time() - updated_at
            if not 0 <= age < _REPO_CACHE_TTL_SECS:
                return False

            infos: dict[str, RepositoryInfo] = {}
            for entry in entries:
                if not isinstance(entry, dict):
                    return False
                path = entry.get("path")
                name = entry.get("name")
                status = entry.get("status")
                if not all(isinstance(value, str) for value in (path, name, status)):
                    return False
                if not all(isinstance(entry.get(field), bool) for field in ("staged", "unstaged")):
                    return False
                if not isinstance(entry.get("message"), str):
                    return False
                if any(
                    value is not None and not isinstance(value, str)
                    for value in (
                        entry.get("branch"),
                        entry.get("last_updated"),
                    )
                ):
                    return False
                if any(
                    value is not None and (isinstance(value, bool) or not isinstance(value, int))
                    for value in (
                        entry.get("last_commit_timestamp"),
                        entry.get("size"),
                    )
                ):
                    return False
                if any(
                    value is not None
                    and (
                        not isinstance(value, list)
                        or any(not isinstance(item, str) for item in value)
                    )
                    for value in (
                        entry.get("staged_files"),
                        entry.get("unstaged_files"),
                    )
                ):
                    return False
                info = RepositoryInfo(
                    path=Path(path),
                    name=name,
                    status=RepoStatus(status),
                    branch=entry.get("branch"),
                    message=entry.get("message", ""),
                    staged=entry.get("staged", False),
                    unstaged=entry.get("unstaged", False),
                    staged_files=entry.get("staged_files"),
                    unstaged_files=entry.get("unstaged_files"),
                    last_updated=entry.get("last_updated"),
                    last_commit_timestamp=entry.get("last_commit_timestamp"),
                    size=entry.get("size"),
                )
                infos[str(info.path)] = info
        except (KeyError, TypeError, ValueError, OSError):
            logger.debug("Ignoring invalid repository cache", exc_info=True)
            return False

        repo_paths = sorted(self.manager.config.repositories, key=self._repo_path_sort_key)
        if len(infos) != len(entries) or set(infos) != {str(path) for path in repo_paths}:
            return False

        self._repo_paths = repo_paths
        self._groups_entries = detect_repo_groups(repo_paths)
        self._results = infos
        self._repos_cache_updated_at = monotonic() - age
        self._repos_cache_saved_at = updated_at
        if not repo_paths:
            self._show_no_repos()
        else:
            self._populate_initial_rows()
            self._update_status(self._build_loaded_status(len(repo_paths), len(repo_paths)))
        return True

    def _repo_path_sort_key(self, path: Path) -> tuple[str, str, str]:
        return (path.parent.name.lower(), str(path.parent).lower(), path.name.lower())

    @work(thread=True)
    def _load_repos(self, *, show_loading: bool = True) -> None:
        self.call_from_thread(self._show_refresh_indicator)
        worker = self._current_worker_or_none()

        def shutdown_requested() -> bool:
            return self._background_shutdown_requested(worker)

        def safe_call(callback, *args, **kwargs) -> None:
            try:
                self.call_from_thread(callback, *args, **kwargs)
            except Exception:
                logger.debug("Suppressed UI update after shutdown", exc_info=True)

        self._repo_paths = sorted(self.manager.config.repositories, key=self._repo_path_sort_key)
        self._groups_entries = detect_repo_groups(self._repo_paths)

        if not self._repo_paths:
            if not shutdown_requested():
                safe_call(self._show_no_repos)
                self._repos_cache_updated_at = monotonic()
                self._save_repos_cache()
            self._repos_refreshing = False
            safe_call(self._hide_refresh_indicator)
            return

        if shutdown_requested():
            self._repos_refreshing = False
            safe_call(self._hide_refresh_indicator)
            return

        if show_loading or not self._results:
            safe_call(self._populate_initial_rows, show_loading=show_loading)

        total = len(self._repo_paths)
        done = 0
        if show_loading:
            safe_call(self._update_status, f"Checking {total} repositories…")

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
                safe_call(self._update_row, info)
                remaining = total - done
                if shutdown_requested():
                    break
                if show_loading and remaining > 0:
                    safe_call(
                        self._update_status,
                        f"{done} done, {remaining} remaining…",
                    )
        finally:
            executor.shutdown(wait=not shutdown_requested(), cancel_futures=shutdown_requested())
            if self._repo_status_executor is executor:
                self._repo_status_executor = None

        if shutdown_requested():
            self._repos_refreshing = False
            safe_call(self._hide_refresh_indicator)
            return

        self._repos_cache_updated_at = monotonic()
        self._save_repos_cache()
        self._repos_refreshing = False
        safe_call(self._hide_refresh_indicator)
        if show_loading and (
            self._search_query or self._sort_column != _DEFAULT_SORT_COLUMN or self._sort_reverse
        ):
            self.call_from_thread(self._apply_filter_and_sort)
        else:
            self.call_from_thread(
                self._update_status,
                self._build_loaded_status(total, total),
            )

    def _repo_cache_expired(self) -> bool:
        updated_at = self._repos_cache_updated_at
        return updated_at is None or monotonic() - updated_at >= _REPO_CACHE_TTL_SECS

    def _refresh_repos(self, *, show_loading: bool) -> None:
        if self._repos_refreshing:
            return
        self._repos_refreshing = True
        if show_loading:
            self._results.clear()
            self._repos_cache_updated_at = None
        self._load_repos(show_loading=show_loading)

    def _populate_initial_rows(self, *, show_loading: bool = True) -> None:
        table = self.query_one("#repo-table", DataTable)
        no_msg = self.query_one("#no-repos-message", Static)
        self._set_table_empty_state(table, no_msg, is_empty=False)
        preserved_row_key = None
        preserved_row_index = None
        restore_focus = False
        if self._resume_selection_tab != "repos":
            preserved_row_key, preserved_row_index, restore_focus = self._capture_table_selection(
                table
            )
        table.clear()
        self._render_repo_path_rows(table, self._repo_paths, show_loading=show_loading)

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
        table = self.query_one("#repo-table", DataTable)
        no_msg = self.query_one("#no-repos-message", Static)
        self._set_table_empty_state(table, no_msg, is_empty=True)
        table.clear()
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
            return lambda info: _changes_sort_key(info)
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

    def _repo_group_count_label(self, group: RepoGroup) -> str:
        repo_label = "repo" if group.repo_count == 1 else "repos"
        return f"[bold cyan][{group.repo_count} {repo_label}][/bold cyan]"

    def _add_repo_group_row(self, table: DataTable, group: RepoGroup) -> None:
        table.add_row(
            self._repo_group_label(group),
            self._repo_group_count_label(group),
            "",
            "",
            "",
            str(group.path),
            key=self._group_row_key(group.path),
        )

    def _add_placeholder_repo_row(
        self, table: DataTable, path: Path, *, grouped: bool, show_loading: bool
    ) -> None:
        value = "... ... ... ..." if show_loading else "—"
        table.add_row(
            f"  {path.name}" if grouped else path.name,
            value,
            value,
            value,
            value,
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

    def _add_repo_path_row(
        self, table: DataTable, path: Path, *, grouped: bool, show_loading: bool
    ) -> None:
        info = self._results.get(str(path))
        if info is None:
            self._add_placeholder_repo_row(table, path, grouped=grouped, show_loading=show_loading)
            return
        self._add_repo_info_row(table, info, grouped=grouped)

    def _render_repo_path_rows(
        self, table: DataTable, paths: list[Path], *, show_loading: bool = True
    ) -> None:
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
            self._add_repo_group_row(table, group)
            if self._repo_group_is_collapsed(group):
                continue
            for path in sorted(group_paths, key=lambda item: item.name.lower()):
                self._add_repo_path_row(table, path, grouped=True, show_loading=show_loading)

        ungrouped_paths = [path for path in paths if path not in grouped_paths]
        shown_repo_count += len(ungrouped_paths)
        for path in sorted(ungrouped_paths, key=lambda item: item.name.lower()):
            self._add_repo_path_row(table, path, grouped=False, show_loading=show_loading)

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
            self._add_repo_group_row(table, group)
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
        no_msg = self.query_one("#no-repos-message", Static)
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
        is_empty = total == 0 and not self._repo_paths and not self._search_query
        self._set_table_empty_state(table, no_msg, is_empty=is_empty)
        table.clear()
        if is_empty:
            self._visible_repo_count = 0
            self._visible_group_count = 0
        else:
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
            msg += "  [space] toggle"
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
            self._refresh_repos(show_loading=True)
