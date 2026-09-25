"""Repository list loading and filtering helpers for the TUI."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path
from time import monotonic, time

from rich.markup import escape
from rich.text import Text
from textual import work
from textual.widgets import DataTable, Static

from ...repo import RepositoryInfo, RepoStatus
from ...storage import load_yaml_mapping, write_yaml_atomic
from .app_groups import RepoGroup, detect_repo_groups
from .constants import (
    _DEFAULT_SORT_COLUMN,
    _REPO_CACHE_TTL_SECS,
    _SORT_COLUMN_NAMES,
)
from .repo_rows import (
    RepoLayout,
    RepoSessions,
    attention_rank,
    group_row,
    name_width,
    repo_header,
    repo_row,
    resolve_repo_layout,
    row_height,
    status_text,
    summarise_sessions,
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
            "config_token": self.manager.config.repository_cache_token(),
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
                    "sync_stale": info.sync_stale,
                    "ahead": info.ahead,
                    "behind": info.behind,
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
            config_token = data.get("config_token")
            entries = data["repositories"]
            if isinstance(updated_at, bool) or not isinstance(updated_at, (int, float)):
                return False
            if config_token != self.manager.config.repository_cache_token():
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
                if not isinstance(entry.get("sync_stale"), bool):
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
                        entry.get("ahead"),
                        entry.get("behind"),
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
                    sync_stale=entry["sync_stale"],
                    ahead=entry.get("ahead") or 0,
                    behind=entry.get("behind") or 0,
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
    def _load_repos(self) -> None:
        worker = self._current_worker_or_none()

        def shutdown_requested() -> bool:
            return self._background_shutdown_requested(worker)

        def safe_call(callback, *args, **kwargs) -> None:
            try:
                self.call_from_thread(callback, *args, **kwargs)
            except Exception:
                logger.debug("Suppressed UI update after shutdown", exc_info=True)

        try:
            self._repo_paths = sorted(
                self.manager.config.repositories, key=self._repo_path_sort_key
            )
            self._groups_entries = detect_repo_groups(self._repo_paths)
            configured_paths = {str(path) for path in self._repo_paths}
            self._results = {
                path: info for path, info in self._results.items() if path in configured_paths
            }

            if not self._repo_paths:
                if not shutdown_requested():
                    safe_call(self._show_no_repos)
                    self._repos_cache_updated_at = monotonic()
                    self._save_repos_cache()
                return

            if shutdown_requested():
                return

            safe_call(self._populate_initial_rows)

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
                    self._repo_results_version += 1
                    safe_call(self._update_row, info)
            finally:
                executor.shutdown(
                    wait=not shutdown_requested(), cancel_futures=shutdown_requested()
                )
                if self._repo_status_executor is executor:
                    self._repo_status_executor = None

            if shutdown_requested():
                return

            self._repos_cache_updated_at = monotonic()
            self._save_repos_cache()
            safe_call(self._apply_filter_and_sort)
        finally:
            self._repos_refreshing = False
            safe_call(self._hide_refresh_indicator)
            if self._repos_refresh_pending and not shutdown_requested():
                self._repos_refresh_pending = False
                safe_call(self._refresh_repos)

    def _refresh_local_statuses(self) -> None:
        """Re-read every repository's worktree and local refs, without the network.

        Keeps the table honest while agents edit files in their sessions; only
        a full refresh fetches from origin.
        """
        if (
            self._active_tab != "repos"
            or self._repos_refreshing
            or self._local_refresh_running
            or self._session_status_tracking_paused
            or not self._results
        ):
            return
        self._local_refresh_running = True
        self._load_local_statuses(list(self._repo_paths), self._repo_results_version)

    @work(thread=True)
    def _load_local_statuses(self, paths: list[Path], version: int) -> None:
        worker = self._current_worker_or_none()
        try:
            changed: list[RepositoryInfo] = []
            for path in paths:
                if self._background_shutdown_requested(worker):
                    return
                previous = self._results.get(str(path))
                if previous is None:
                    continue
                try:
                    info = self.manager.get_repository_status(path)
                except Exception:
                    logger.debug("local status failed for %s", path, exc_info=True)
                    continue
                # Nothing was fetched, so whether origin was reachable is unchanged.
                info = replace(info, sync_stale=previous.sync_stale)
                if info != previous:
                    changed.append(info)
            if changed and not self._background_shutdown_requested(worker):
                self.call_from_thread(self._apply_local_statuses, changed, version)
        finally:
            self._local_refresh_running = False

    def _apply_local_statuses(self, infos: list[RepositoryInfo], version: int) -> None:
        # A fetch that landed meanwhile knows more than this local read.
        if version != self._repo_results_version or self._repos_refreshing:
            return
        for info in infos:
            self._results[str(info.path)] = info
            self._update_row(info)

    def _repo_cache_expired(self) -> bool:
        updated_at = self._repos_cache_updated_at
        return updated_at is None or monotonic() - updated_at >= _REPO_CACHE_TTL_SECS

    def _reload_config_if_changed(self) -> bool:
        try:
            changed = self.manager.config.reload_if_changed()
        except ValueError as exc:
            # The file was edited by hand into something unloadable. Keep the
            # last good configuration and say so instead of crashing.
            logger.warning("config reload failed: %s", exc)
            self._update_status(f"config not reloaded: {exc}")
            return False
        if not changed:
            return False
        self._repos_cache_updated_at = None
        self._repos_cache_saved_at = None
        return True

    def _refresh_repos(self) -> None:
        """Fetch every repository again, in place.

        What is on screen stays until each fresh result replaces it, so a
        refresh never blanks the table; repositories new to the config show
        as checking until theirs arrives.
        """
        config_changed = self._reload_config_if_changed()
        if self._repos_refreshing:
            # The running load read the repository list before this change;
            # run again once it finishes rather than dropping the change.
            self._repos_refresh_pending = self._repos_refresh_pending or config_changed
            return
        self._repos_refreshing = True
        self._show_refresh_indicator()
        self._load_repos()

    def _populate_initial_rows(self) -> None:
        """Paint every configured repository, loaded or still loading."""
        self._apply_filter_and_sort(update_status=False)

    def _update_row(self, info: RepositoryInfo) -> None:
        try:
            self._repaint_repo_row(info)
        except Exception:
            # A late result can land while the table is torn down.
            logger.debug("Failed to update repo row %s", info.path, exc_info=True)

    def _repaint_repo_row(self, info: RepositoryInfo) -> None:
        table = self.query_one("#repo-table", DataTable)
        infos, loading = self._repo_row_infos()
        if self._resolve_repo_layout(infos, loading) != self._repo_layout:
            self._apply_filter_and_sort(update_status=False)
            return
        row_key = str(info.path)
        if row_key not in table.rows:
            return
        sessions = self._repo_sessions()
        group = self._repo_group_containing(info.path)
        row = repo_row(
            info,
            self._repo_layout,
            self._palette,
            grouped=group is not None,
            sessions=sessions.get(info.path),
        )
        if not self._set_repo_row(table, row_key, row):
            return
        if group is not None:
            self._update_group_row(table, group, infos, loading, sessions)

    def _set_repo_row(self, table: DataTable, row_key: str, row) -> bool:
        """Repaint one row in place; if it now needs more or fewer lines, rebuild.

        Returns False when the table was rebuilt instead.
        """
        if table.rows[row_key].height != row_height(row):
            self._apply_filter_and_sort(update_status=False)
            return False
        table.update_cell(row_key, self._col_keys[0], row)
        return True

    def _update_group_row(
        self,
        table: DataTable,
        group: RepoGroup,
        infos: list[RepositoryInfo],
        loading: set[Path],
        sessions: dict[Path, RepoSessions],
    ) -> None:
        key = self._group_row_key(group.path)
        if key not in table.rows:
            return
        members = set(group.repositories)
        group_infos = [info for info in infos if info.path in members]
        lead = table.get_row_index(key) > 0
        table.update_cell(
            key,
            self._col_keys[0],
            self._repo_group_row(group, group_infos, loading, sessions, lead=lead),
        )

    def _repo_group_containing(self, path: Path) -> RepoGroup | None:
        for group in self._groups_entries:
            if path in group.repositories:
                return group
        return None

    def _repo_sessions(self) -> dict[Path, RepoSessions]:
        """Live sessions per repository, matched through the session name's repo segment."""
        from ...integrations.tmux.core import _repo_session_name_segment

        by_slug: dict[str, Path] = {}
        for path in self._repo_paths:
            slug = self._repo_slugs.get(path)
            if slug is None:
                slug = self._repo_slugs[path] = _repo_session_name_segment(path)
            by_slug[slug] = path
        grouped: dict[Path, list[dict[str, str]]] = {}
        for entry in self._sessions_entries:
            path = by_slug.get(entry.get("repo_slug", ""))
            if path is not None:
                grouped.setdefault(path, []).append(entry)
        return {path: summarise_sessions(entries) for path, entries in grouped.items()}

    def _refresh_repo_session_cells(self) -> None:
        """Repaint the rows whose sessions changed, without rebuilding the table."""
        sessions = self._repo_sessions()
        if sessions == self._rendered_repo_sessions:
            return
        changed = {
            path
            for path in set(sessions) | set(self._rendered_repo_sessions)
            if sessions.get(path) != self._rendered_repo_sessions.get(path)
        }
        self._rendered_repo_sessions = sessions
        try:
            table = self.query_one("#repo-table", DataTable)
        except Exception:
            return
        if self._sort_column != _DEFAULT_SORT_COLUMN:
            # Sessions can move rows under these orders.
            self._apply_filter_and_sort(update_status=False)
            return
        infos, loading = self._repo_row_infos()
        by_path = {info.path: info for info in infos}
        groups: dict[Path, RepoGroup] = {}
        for path in changed:
            info = by_path.get(path)
            if info is None or str(path) not in table.rows:
                continue
            group = self._repo_group_containing(path)
            if group is not None:
                groups[group.path] = group
            row = repo_row(
                info,
                self._repo_layout,
                self._palette,
                grouped=group is not None,
                loading=path in loading,
                sessions=sessions.get(path),
            )
            if not self._set_repo_row(table, str(path), row):
                return
        for group in groups.values():
            self._update_group_row(table, group, infos, loading, sessions)

    def _resolve_repo_layout(self, infos: list[RepositoryInfo], loading: set[Path]) -> RepoLayout:
        grouped = {path for group in self._groups_entries for path in group.repositories}
        names = (
            name_width(info, grouped=info.path in grouped, loading=info.path in loading)
            for info in infos
        )
        statuses = (
            status_text(info, self._palette).cell_len for info in infos if info.path not in loading
        )
        # Mid-refresh a column narrowing and widening again reads as flicker.
        floor = self._repo_layout.status if self._repos_refreshing else 0
        return resolve_repo_layout(names, statuses, self.size.width, min_status=floor)

    def _apply_repo_layout(self, table: DataTable, layout: RepoLayout) -> None:
        self._repo_layout = layout
        col_keys = getattr(self, "_col_keys", None)
        if not col_keys:
            return
        column = table.columns[col_keys[0]]
        column.auto_width = False
        column.width = layout.cell_width
        column.label = repo_header(layout)
        table.refresh()

    def _show_no_repos(self) -> None:
        table = self.query_one("#repo-table", DataTable)
        no_msg = self.query_one("#no-repos-message", Static)
        self._set_table_empty_state(table, no_msg, is_empty=True)
        table.clear()
        self._visible_repo_count = 0
        self._visible_group_count = 0
        self._update_status("No repositories linked")

    def _sort_key_func(self, sessions: dict[Path, RepoSessions] | None = None):
        col = self._sort_column
        sessions = sessions or {}
        if col == 1:
            return lambda info: (attention_rank(info, sessions.get(info.path)), info.name.lower())
        if col == 2:
            return lambda info: (info.branch or "").lower()
        if col == 3:
            return lambda info: info.last_commit_timestamp or 0
        if col == 4:
            return lambda info: (
                -(sessions[info.path].count if info.path in sessions else 0),
                info.name.lower(),
            )
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

    def _repo_group_row(
        self,
        group: RepoGroup,
        infos: list[RepositoryInfo],
        loading: set[Path],
        sessions: dict[Path, RepoSessions],
        *,
        lead: bool,
    ):
        return group_row(
            group,
            infos,
            loading,
            sessions,
            self._repo_layout,
            self._palette,
            collapsed=self._repo_group_is_collapsed(group),
            lead=lead,
        )

    def _sorted_repo_infos(
        self,
        infos: list[RepositoryInfo],
        loading: set[Path],
        sessions: dict[Path, RepoSessions] | None = None,
    ) -> list[RepositoryInfo]:
        """Sort loaded repositories; ones still loading follow, by name."""
        loaded = [info for info in infos if info.path not in loading]
        loaded.sort(key=self._sort_key_func(sessions), reverse=self._sort_reverse)
        pending = sorted(
            (info for info in infos if info.path in loading), key=lambda info: info.name.lower()
        )
        return loaded + pending

    def _repo_row_entry(
        self,
        info: RepositoryInfo,
        loading: set[Path],
        sessions: dict[Path, RepoSessions],
        *,
        grouped: bool,
    ) -> tuple[str, Text, int]:
        row = repo_row(
            info,
            self._repo_layout,
            self._palette,
            grouped=grouped,
            loading=info.path in loading,
            sessions=sessions.get(info.path),
        )
        return str(info.path), row, row_height(row)

    def _render_repo_info_rows(
        self,
        table: DataTable,
        infos: list[RepositoryInfo],
        loading: set[Path] | None = None,
    ) -> None:
        loading = loading or set()
        sessions = self._repo_sessions()
        self._rendered_repo_sessions = sessions
        infos_by_path = {info.path: info for info in infos}
        grouped_paths = {path for group in self._groups_entries for path in group.repositories}
        shown_group_count = 0
        rows: list[tuple[str, Text, int]] = []

        # Standalone repositories come first: after a group they would read
        # as part of it.
        ungrouped_infos = [info for info in infos if info.path not in grouped_paths]
        shown_repo_count = len(ungrouped_infos)
        for info in self._sorted_repo_infos(ungrouped_infos, loading, sessions):
            rows.append(self._repo_row_entry(info, loading, sessions, grouped=False))

        for group in self._groups_entries:
            group_infos = [
                infos_by_path[path] for path in group.repositories if path in infos_by_path
            ]
            if not group_infos:
                continue
            shown_group_count += 1
            shown_repo_count += len(group_infos)
            # A blank line above every group but a first row keeps groups apart.
            lead = bool(rows)
            heading = self._repo_group_row(group, group_infos, loading, sessions, lead=lead)
            rows.append((self._group_row_key(group.path), heading, 2 if lead else 1))
            if self._repo_group_is_collapsed(group):
                continue
            for info in self._sorted_repo_infos(group_infos, loading, sessions):
                rows.append(self._repo_row_entry(info, loading, sessions, grouped=True))

        self._show_repo_rows(table, rows)
        self._visible_repo_count = shown_repo_count
        self._visible_group_count = shown_group_count

    def _show_repo_rows(self, table: DataTable, rows: list[tuple[str, Text, int]]) -> None:
        """Put *rows* on screen without a flash.

        When the same rows are there in the same order, only the lines that
        changed are repainted, and cursor and scroll stay put. Anything else
        is rebuilt inside one batch, so no half-built table is ever drawn.
        """
        shown = [(str(row.key.value), row.height) for row in table.ordered_rows]
        if shown == [(key, height) for key, _, height in rows]:
            for key, row, _ in rows:
                if self._shown_repo_rows.get(key) != row:
                    table.update_cell(key, self._col_keys[0], row)
        else:
            with self.batch_update():
                table.clear()
                for key, row, height in rows:
                    table.add_row(row, key=key, height=height)
        self._shown_repo_rows = {key: row for key, row, _ in rows}

    def _repo_row_infos(self) -> tuple[list[RepositoryInfo], set[Path]]:
        """Every configured repository's info, with a placeholder for each one
        still loading, and the set of those placeholders' paths."""
        if not self._repo_paths:
            return list(self._results.values()), set()
        infos: list[RepositoryInfo] = []
        loading: set[Path] = set()
        for path in self._repo_paths:
            info = self._results.get(str(path))
            if info is None:
                info = RepositoryInfo(path, path.name, RepoStatus.UNKNOWN)
                loading.add(path)
            infos.append(info)
        return infos, loading

    def _apply_filter_and_sort(self, *, update_status: bool = True) -> None:
        table = self.query_one("#repo-table", DataTable)
        no_msg = self.query_one("#no-repos-message", Static)
        preserved_row_key = None
        preserved_row_index = None
        restore_focus = False
        if self._resume_selection_tab != "repos":
            preserved_row_key, preserved_row_index, restore_focus = self._capture_table_selection(
                table
            )

        infos, loading = self._repo_row_infos()
        self._apply_repo_layout(table, self._resolve_repo_layout(infos, loading))
        total = len(infos)
        infos = self._filter_repo_infos_for_search(infos)
        is_empty = total == 0 and not self._search_query
        self._set_table_empty_state(table, no_msg, is_empty=is_empty)
        if is_empty:
            self._show_repo_rows(table, [])
            self._visible_repo_count = 0
            self._visible_group_count = 0
        else:
            self._render_repo_info_rows(table, infos, loading)

        if self._resume_selection_tab == "repos":
            self._restore_resume_selection("repos")
        else:
            self._restore_table_selection(
                table,
                preserved_row_key,
                preserved_row_index,
                restore_focus=restore_focus,
            )
        if update_status and self._active_tab == "repos":
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
            msg += "  [space] toggle  [shift+space] toggle all"
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
        self._rerender_repo_rows()

    def action_toggle_all_groups(self) -> None:
        """Collapse every group, or expand them all once none is left open."""
        if self._active_tab != "repos":
            return
        group_keys = {str(group.path) for group in self._groups_entries}
        if not group_keys:
            return
        if group_keys <= self._collapsed_groups:
            self._collapsed_groups.difference_update(group_keys)
        else:
            self._collapsed_groups.update(group_keys)
        self._rerender_repo_rows()

    def _rerender_repo_rows(self) -> None:
        """Repaint the repositories table from the current state."""
        self._apply_filter_and_sort()

    def action_refresh(self) -> None:
        if self._active_tab == "sessions":
            self._load_sessions()
        elif self._active_tab == "panels":
            self._load_panels()
        else:
            # The last results stay up while fresh ones arrive; the footer
            # shows the refresh.
            self._refresh_repos()
