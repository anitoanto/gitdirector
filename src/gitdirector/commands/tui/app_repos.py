"""Repository list loading and filtering helpers for the TUI."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from rich.markup import escape
from textual import work
from textual.widgets import DataTable, Static

from ...repo import RepositoryInfo, RepoStatus
from .constants import (
    _DEFAULT_SORT_COLUMN,
    _SORT_COLUMN_NAMES,
    _STATUS_LABEL,
    _STATUS_ORDER,
    _changes_label,
)

logger = logging.getLogger(__name__)


class ConsoleReposMixin:
    @work(thread=True)
    def _load_repos(self) -> None:
        self._repo_paths = sorted(
            self.manager.config.repositories, key=lambda path: path.name.lower()
        )

        if not self._repo_paths:
            self.call_from_thread(self._show_no_repos)
            return

        self.call_from_thread(self._populate_initial_rows)

        total = len(self._repo_paths)
        done = 0
        self.call_from_thread(self._update_status, f"Checking {total} repositories…")

        from ...integrations.tmux import (
            _repo_session_name_segment,
            _sanitize_repo_name,
            list_all_gd_sessions,
        )

        all_sessions = list_all_gd_sessions()
        sessions_by_repo: dict[str, int] = {}
        for entry in all_sessions:
            repo_slug = entry.get("repo_slug", entry["repo"])
            sessions_by_repo[repo_slug] = sessions_by_repo.get(repo_slug, 0) + 1

        with ThreadPoolExecutor(max_workers=self.manager.config.max_workers) as executor:
            futures = {
                executor.submit(self.manager.get_repository_status, path, fetch=True): path
                for path in self._repo_paths
            }
            for future in as_completed(futures):
                path = futures[future]
                try:
                    info = future.result()
                except Exception as exc:
                    info = RepositoryInfo(path, path.name, RepoStatus.UNKNOWN, None, str(exc))
                self._results[str(info.path)] = info
                done += 1
                repo_slug = _repo_session_name_segment(info.path)
                sessions_count = sessions_by_repo.get(
                    repo_slug,
                    sessions_by_repo.get(_sanitize_repo_name(info.path.name), 0),
                )
                self._sessions_cache[str(info.path)] = sessions_count
                self.call_from_thread(self._update_row, info, sessions_count)
                remaining = total - done
                if remaining > 0:
                    self.call_from_thread(
                        self._update_status,
                        f"{done} done, {remaining} remaining…",
                    )

        if self._search_query or self._sort_column != _DEFAULT_SORT_COLUMN or self._sort_reverse:
            self.call_from_thread(self._apply_filter_and_sort)
        else:
            self.call_from_thread(
                self._update_status,
                self._build_loaded_status(total, total),
            )

    def _populate_initial_rows(self) -> None:
        table = self.query_one("#repo-table", DataTable)
        preserved_row_key = None
        preserved_row_index = None
        restore_focus = False
        if self._resume_selection_tab != "repos":
            preserved_row_key, preserved_row_index, restore_focus = self._capture_table_selection(
                table
            )
        table.clear()
        for path in self._repo_paths:
            table.add_row(
                path.name,
                "... ... ... ...",
                "... ... ... ...",
                "... ... ... ...",
                "... ... ... ... ... ...",
                "...",
                str(path),
                key=str(path),
            )

        if self._resume_selection_tab == "repos":
            self._restore_resume_selection("repos")
        else:
            self._restore_table_selection(
                table,
                preserved_row_key,
                preserved_row_index,
                restore_focus=restore_focus,
            )

    def _update_row(self, info: RepositoryInfo, sessions: int = 0) -> None:
        self._sessions_cache[str(info.path)] = sessions
        table = self.query_one("#repo-table", DataTable)
        row_key = str(info.path)
        ck = self._col_keys
        try:
            table.update_cell(row_key, ck[1], _STATUS_LABEL.get(info.status, "unknown"))
            table.update_cell(row_key, ck[2], info.branch or "—")
            table.update_cell(row_key, ck[3], _changes_label(info))
            table.update_cell(row_key, ck[4], info.last_updated or "—")
            table.update_cell(row_key, ck[5], str(sessions) if sessions > 0 else "—")
        except Exception:
            logger.debug("Failed to update repo row %s", row_key, exc_info=True)

    def _show_no_repos(self) -> None:
        self.query_one("#repo-table", DataTable).display = False
        self.query_one("#no-repos-message", Static).display = True
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
            return lambda info: self._sessions_cache.get(str(info.path), 0)
        if col == 6:
            return lambda info: str(info.path).lower()
        return lambda info: info.name.lower()

    def _apply_filter_and_sort(self) -> None:
        table = self.query_one("#repo-table", DataTable)
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

        if self._search_query:
            query = self._search_query.lower()
            infos = [
                info
                for info in infos
                if query in info.name.lower()
                or query in (info.branch or "").lower()
                or query in str(info.path).lower()
            ]

        infos.sort(key=self._sort_key_func(), reverse=self._sort_reverse)

        for info in infos:
            sessions = self._sessions_cache.get(str(info.path), 0)
            table.add_row(
                info.name,
                _STATUS_LABEL.get(info.status, "unknown"),
                info.branch or "—",
                _changes_label(info),
                info.last_updated or "—",
                str(sessions) if sessions > 0 else "—",
                str(info.path),
                key=str(info.path),
            )

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
        msg = f"{count_str} {label} loaded"

        indicators: list[str] = []
        if self._search_query:
            indicators.append(f"filter: '{escape(self._search_query)}'")
        if self._sort_column != _DEFAULT_SORT_COLUMN or self._sort_reverse:
            direction = "▼" if self._sort_reverse else "▲"
            indicators.append(f"sort: {_SORT_COLUMN_NAMES[self._sort_column]} {direction}")
        if indicators:
            msg += f"  ({', '.join(indicators)})"

        msg += "   ↑↓/jk navigate  [enter] open  g git  / search  s sort  r refresh  q quit"
        if self._search_query:
            msg += "  [esc] clear search"
        if self._waiting_count > 0:
            waiting = self._waiting_count
            waiting_label = "session" if waiting == 1 else "sessions"
            msg += f"  ⟐ {waiting} {waiting_label} waiting"
        return msg

    def action_refresh(self) -> None:
        if self._active_tab == "sessions":
            self._load_sessions()
        elif self._active_tab == "panels":
            self._load_panels()
        else:
            self._results.clear()
            self._sessions_cache.clear()
            self._load_repos()
