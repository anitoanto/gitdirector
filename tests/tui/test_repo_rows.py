"""The repositories table: composed rows, the local refresh and the status cache."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

from gitdirector.commands.tui import GitDirectorConsole
from gitdirector.commands.tui.app_groups import RepoGroup
from gitdirector.commands.tui.constants import TablePalette
from gitdirector.commands.tui.repo_rows import (
    RepoSessions,
    attention_rank,
    group_row,
    repo_header,
    repo_row,
    resolve_repo_layout,
    sessions_text,
    status_text,
    updated_label,
)
from gitdirector.repo import RepoStatus

from .conftest import _make_info, _mock_manager, repo_row_text

PALETTE = TablePalette(success="green", yellow="yellow", muted="grey50", primary="magenta")
NOW = 1_000_000_000


class TestStatusText:
    def test_clean_and_in_step_says_nothing(self):
        assert status_text(_make_info(), PALETTE).plain == ""

    def test_spells_out_what_is_pending(self):
        info = replace(
            _make_info(status=RepoStatus.DIVERGED, staged=True, unstaged=True),
            ahead=1,
            behind=3,
            staged_files=["a", "b"],
            unstaged_files=["c"],
        )
        assert status_text(info, PALETTE).plain == (
            "↑1 to push · ↓3 to pull · 2 staged · 1 changed"
        )

    def test_unknown_sync_and_offline_are_named(self):
        info = _make_info(status=RepoStatus.UNKNOWN)
        info = replace(info, message="No origin/main branch", sync_stale=True)
        assert status_text(info, PALETTE).plain == "no remote branch · offline"

    def test_loading(self):
        assert status_text(_make_info(), PALETTE, loading=True).plain == "checking…"


class TestUpdatedLabel:
    def test_units_line_up_under_a_two_column_number(self):
        assert updated_label(NOW - 30, NOW) == "now"
        assert updated_label(NOW - 5 * 60, NOW) == " 5m"
        assert updated_label(NOW - 3 * 3600, NOW) == " 3h"
        assert updated_label(NOW - 6 * 86_400, NOW) == " 6d"
        assert updated_label(NOW - 21 * 86_400, NOW) == " 3w"
        assert updated_label(NOW - 400 * 86_400, NOW) == " 1y"
        assert updated_label(None, NOW) == " -"


class TestSessionsText:
    def test_one_dot_per_session_by_type(self):
        sessions = RepoSessions((("claude-auto", "running"), ("shell", "idle")))
        assert sessions_text(sessions, PALETTE).plain == "● claude-auto  ● shell"

    def test_a_waiting_session_is_yellow(self):
        text = sessions_text(RepoSessions((("claude", "waiting"),)), PALETTE)
        assert text.plain == "● claude"
        assert all("yellow" in str(span.style) for span in text.spans)


class TestSessionsWrap:
    def test_sessions_that_do_not_fit_wrap_inside_their_column(self):
        layout = resolve_repo_layout([20], [12], 90)
        many = RepoSessions(tuple((f"claude-auto-{n}", "running") for n in range(6)))
        row = repo_row(_make_info("alpha"), layout, PALETTE, grouped=False, sessions=many)
        lines = row.plain.split("\n")
        assert len(lines) > 1
        start = lines[0].index("● claude-auto-0")
        for line in lines[1:]:
            # Continuation lines leave the other columns empty.
            assert line[:start].strip() == ""
            assert line[start:].startswith("● claude-auto-")
        shown = " ".join(lines)
        assert all(f"claude-auto-{n}" in shown for n in range(6))
        assert "…" not in shown

    def test_a_row_that_fits_stays_one_line(self):
        layout = resolve_repo_layout([20], [12], 120)
        two = RepoSessions((("claude", "running"), ("shell", "idle")))
        row = repo_row(_make_info("alpha"), layout, PALETTE, grouped=False, sessions=two)
        assert "\n" not in row.plain


class TestAttention:
    def test_waiting_outranks_everything(self):
        behind = _make_info(status=RepoStatus.BEHIND)
        waiting = RepoSessions((("claude", "waiting"),))
        assert attention_rank(_make_info(), waiting) < attention_rank(behind)

    def test_order(self):
        ranks = [
            attention_rank(_make_info(status=RepoStatus.DIVERGED)),
            attention_rank(_make_info(status=RepoStatus.BEHIND)),
            attention_rank(_make_info(status=RepoStatus.AHEAD)),
            attention_rank(_make_info(unstaged=True)),
            attention_rank(_make_info(status=RepoStatus.UNKNOWN)),
            attention_rank(_make_info()),
        ]
        assert ranks == sorted(ranks)


class TestRows:
    def test_columns_line_up_between_header_and_rows(self):
        layout = resolve_repo_layout([20], [12], 100)
        header = repo_header(layout).plain
        info = _make_info("alpha", unstaged=True, last_commit_timestamp=NOW - 3600)
        row = repo_row(info, layout, PALETTE, grouped=False, now=NOW).plain
        assert header.index("Updated") == row.index(" 1h")
        assert header.index("Status") == row.index("changed")
        assert len(header) == len(row) == layout.cell_width

    def test_branch_shows_only_when_not_the_default(self):
        layout = resolve_repo_layout([30], [], 100)
        main = repo_row(_make_info("a", branch="main"), layout, PALETTE, grouped=False).plain
        feature = repo_row(_make_info("a", branch="feat/x"), layout, PALETTE, grouped=False).plain
        assert "main" not in main
        assert "a  feat/x" in feature

    def test_open_group_heading_is_just_its_name(self):
        group = RepoGroup(Path("/tmp/work"), (Path("/tmp/work/a"), Path("/tmp/work/b")))
        layout = resolve_repo_layout([10], [], 80)
        line = group_row(group, [], set(), {}, layout, PALETTE, collapsed=False).plain
        assert line.strip() == "▾ work"

    def test_folded_group_says_what_it_hides(self):
        a = _make_info("a", Path("/tmp/work/a"), unstaged=True)
        b = _make_info("b", Path("/tmp/work/b"))
        group = RepoGroup(Path("/tmp/work"), (a.path, b.path))
        layout = resolve_repo_layout([10], [], 80)
        waiting = {b.path: RepoSessions((("claude", "waiting"),))}
        line = group_row(group, [a, b], set(), waiting, layout, PALETTE, collapsed=True).plain
        assert line.strip() == "▸ work   2 repos · 2 need attention · ● 1 waiting"

    def test_a_leading_group_row_opens_with_a_blank_line(self):
        group = RepoGroup(Path("/tmp/work"), (Path("/tmp/work/a"),))
        layout = resolve_repo_layout([10], [], 80)
        lines = group_row(group, [], set(), {}, layout, PALETTE, collapsed=False, lead=True).plain
        blank, heading = lines.split("\n")
        assert blank.strip() == "" and heading.strip() == "▾ work"


class TestLocalRefresh:
    async def test_picks_up_worktree_changes_without_fetching(self):
        info = _make_info("alpha", Path("/tmp/alpha"))
        app = GitDirectorConsole()
        app.manager = _mock_manager([info])
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            dirty = replace(info, unstaged=True, unstaged_files=["a", "b"])
            app.manager.get_repository_status.side_effect = None
            app.manager.get_repository_status.return_value = dirty

            app._refresh_local_statuses()
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert "2 changed" in repo_row_text(app, str(info.path))
            app.manager.get_repository_status.assert_called_with(info.path)

    def test_a_fetch_that_landed_meanwhile_wins(self):
        info = _make_info("alpha", Path("/tmp/alpha"))
        app = GitDirectorConsole()
        app._results = {str(info.path): info}
        app._update_row = MagicMock()
        stale = replace(info, unstaged=True)

        app._repo_results_version = 2
        app._apply_local_statuses([stale], version=1)

        assert app._results[str(info.path)] is info
        app._update_row.assert_not_called()

    def test_skipped_off_the_repos_tab(self):
        app = GitDirectorConsole()
        app._active_tab = "sessions"
        app._results = {"x": _make_info()}
        app._load_local_statuses = MagicMock()

        app._refresh_local_statuses()

        app._load_local_statuses.assert_not_called()


class TestRowGrowsWithSessions:
    async def test_new_sessions_that_wrap_grow_the_row(self):
        from textual.widgets import DataTable

        from gitdirector.integrations.tmux.core import _repo_session_name_segment

        info = _make_info("alpha", Path("/tmp/alpha"))
        slug = _repo_session_name_segment(info.path)
        app = GitDirectorConsole()
        app.manager = _mock_manager([info])
        async with app.run_test(size=(90, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            table = app.query_one("#repo-table", DataTable)
            assert table.rows[str(info.path)].height == 1

            app._sessions_entries = [
                {
                    "session_name": f"gd/{slug}/claude-auto/{n}",
                    "repo_slug": slug,
                    "purpose": f"claude-auto-{n}",
                    "status": "running",
                }
                for n in range(1, 7)
            ]
            app._refresh_repo_session_cells()
            await pilot.pause()

            assert table.rows[str(info.path)].height > 1
            assert "claude-auto-6" in repo_row_text(app, str(info.path))


class TestStatusCache:
    def test_ahead_and_behind_survive_a_round_trip(self):
        info = replace(
            _make_info("alpha", Path("/tmp/alpha"), RepoStatus.DIVERGED), ahead=2, behind=5
        )
        app = GitDirectorConsole()
        app.manager = _mock_manager([info])
        app._results = {str(info.path): info}
        app._save_repos_cache()
        app._results = {}
        app._populate_initial_rows = MagicMock()
        app._update_status = MagicMock()

        assert app._load_repos_from_cache() is True
        cached = app._results[str(info.path)]
        assert (cached.ahead, cached.behind) == (2, 5)


class TestSeamlessRefresh:
    async def test_unchanged_rows_are_repainted_in_place(self):
        from textual.widgets import DataTable

        info = _make_info("alpha", Path("/tmp/alpha"))
        app = GitDirectorConsole()
        app.manager = _mock_manager([info])
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            table = app.query_one("#repo-table", DataTable)
            table.clear = MagicMock(wraps=table.clear)

            app._results[str(info.path)] = replace(info, unstaged=True, unstaged_files=["a"])
            app._apply_filter_and_sort()
            await pilot.pause()

            # Same rows, same order: no clear and rebuild, just the new line.
            table.clear.assert_not_called()
            assert "1 changed" in repo_row_text(app, str(info.path))

    async def test_a_refresh_never_blanks_the_table(self):
        info = _make_info("alpha", Path("/tmp/alpha"))
        app = GitDirectorConsole()
        app.manager = _mock_manager([info])
        async with app.run_test(size=(120, 30)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()
            app._load_repos = MagicMock()
            await pilot.press("r")
            await pilot.pause()
            assert "checking" not in repo_row_text(app, str(info.path))

    def test_status_column_does_not_narrow_mid_refresh(self):
        app = GitDirectorConsole()
        app._repo_paths = [Path("/tmp/alpha")]
        app._repo_layout = resolve_repo_layout([10], [30], 0)
        app._repos_refreshing = True
        infos = [_make_info("alpha", Path("/tmp/alpha"))]
        assert app._resolve_repo_layout(infos, set()).status == 30
        app._repos_refreshing = False
        assert app._resolve_repo_layout(infos, set()).status < 30
