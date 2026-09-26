"""Composed rows for the repositories table.

Each row is one line drawn into the table's single column, so a group header
reads as a heading while the repositories under it keep aligned columns. A
repository that needs nothing shows only its name and last commit: a branch
other than main, pending work and live sessions appear only when present, in
words rather than symbols.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from time import time

from rich.cells import cell_len
from rich.text import Text

from ...repo import RepositoryInfo, RepoStatus
from .app_groups import RepoGroup
from .constants import TablePalette

CELL_PADDING = 1
# The tab's margin, the table's ``padding: 0 1`` and the scrollbar, less the
# padding each line draws itself.
_TABLE_CHROME_WIDTH = 5
_FALLBACK_TOTAL_WIDTH = 100
_COL_GAP = 3
# Top-level rows (groups and standalone repositories) start past the group
# marker; a group's repositories sit one level in.
_MARKER_WIDTH = 2
_CHILD_INDENT = 2
_BRANCH_GAP = 2
_MIN_NAME_WIDTH = 16
_MAX_NAME_WIDTH = 48
# Room for the loading placeholder.
_MIN_STATUS_WIDTH = 9
_MAX_STATUS_WIDTH = 52
_MIN_SESSIONS_WIDTH = 12
_DEFAULT_BRANCHES = frozenset({"main", "master"})
_EXPANDED = "▾"
_COLLAPSED = "▸"
_WEEK = 7 * 86_400

_HEADERS = ("Repository", "Updated", "Status", "Sessions")
_UPDATED_WIDTH = len("Updated")

# Lower sorts first under "Needs attention".
_WAITING_RANK = 0
_SYNC_RANK = {RepoStatus.DIVERGED: 1, RepoStatus.BEHIND: 2, RepoStatus.AHEAD: 3}
_DIRTY_RANK = 4
_UNKNOWN_RANK = 5
_CLEAN_RANK = 6
_LOADING_RANK = 7


@dataclass(frozen=True)
class RepoLayout:
    name: int
    updated: int
    status: int
    sessions: int

    @property
    def total(self) -> int:
        return self.name + self.updated + self.status + self.sessions + _COL_GAP * 3

    @property
    def cell_width(self) -> int:
        return self.total + CELL_PADDING * 2


@dataclass(frozen=True)
class RepoSessions:
    """The live sessions of one repository: ``(purpose, status)`` in name order."""

    sessions: tuple[tuple[str, str], ...] = ()

    @property
    def count(self) -> int:
        return len(self.sessions)

    @property
    def waiting(self) -> int:
        return sum(status == "waiting" for _, status in self.sessions)


def summarise_sessions(entries: Iterable[dict[str, str]]) -> RepoSessions:
    return RepoSessions(
        tuple((entry.get("purpose", ""), entry.get("status", "")) for entry in entries)
    )


def _count(items: list[str] | None) -> str:
    return f"{len(items)} " if items else ""


def _status_parts(info: RepositoryInfo, palette: TablePalette) -> list[tuple[str, str]]:
    attention = f"bold {palette.yellow}"
    parts: list[tuple[str, str]] = []
    if info.status in (RepoStatus.AHEAD, RepoStatus.DIVERGED):
        parts.append((f"↑{info.ahead or ''} to push", attention))
    if info.status in (RepoStatus.BEHIND, RepoStatus.DIVERGED):
        parts.append((f"↓{info.behind or ''} to pull", attention))
    if info.staged:
        parts.append((f"{_count(info.staged_files)}staged", f"bold {palette.success}"))
    if info.unstaged:
        parts.append((f"{_count(info.unstaged_files)}changed", attention))
    if info.status is RepoStatus.UNKNOWN:
        label = "no remote branch" if info.message.startswith("No origin/") else "sync unknown"
        parts.append((label, palette.muted))
    if info.sync_stale:
        parts.append(("offline", palette.muted))
    return parts


def status_text(info: RepositoryInfo, palette: TablePalette, *, loading: bool = False) -> Text:
    """``↑1 to push · 2 staged · 5 changed``; empty when in step with origin and clean."""
    if loading:
        return Text("checking…", style=palette.muted)
    text = Text()
    for index, (label, style) in enumerate(_status_parts(info, palette)):
        if index:
            text.append(" · ", style=palette.muted)
        text.append(label, style=style)
    return text


_UNITS = (
    (31_536_000, "y"),
    (2_592_000, "mo"),
    (_WEEK, "w"),
    (86_400, "d"),
    (3600, "h"),
    (60, "m"),
)


def updated_label(timestamp: int | None, now: float | None = None) -> str:
    """How long ago the last commit was: `` 4h``, ``12mo``, ``now``.

    The number is right-aligned in two columns so the units line up.
    """
    if timestamp is None:
        return " -"
    seconds = max(0, int((time() if now is None else now) - timestamp))
    for size, unit in _UNITS:
        if seconds >= size:
            return f"{min(seconds // size, 99):>2}{unit}"
    return "now"


def _session_items(sessions: RepoSessions | None, palette: TablePalette) -> list[Text]:
    items = []
    for purpose, status in sessions.sessions if sessions is not None else ():
        if status == "waiting":
            waiting = f"bold {palette.yellow}"
            items.append(Text.assemble(("● ", waiting), (purpose, waiting)))
        elif status == "pending":
            items.append(Text.assemble(("◐ ", palette.pending), purpose))
        else:
            items.append(Text.assemble(("● ", palette.success), purpose))
    return items


def sessions_text(sessions: RepoSessions | None, palette: TablePalette) -> Text:
    """``● claude-auto  ● shell``: one dot per live session, yellow while it waits."""
    return Text("  ").join(_session_items(sessions, palette))


def sessions_lines(sessions: RepoSessions | None, palette: TablePalette, width: int) -> list[Text]:
    """The sessions wrapped to *width*, whole sessions per line."""
    lines = [Text()]
    for item in _session_items(sessions, palette):
        line = lines[-1]
        if line and line.cell_len + 2 + item.cell_len > width:
            lines.append(Text())
            line = lines[-1]
        if line:
            line.append("  ")
        line.append_text(item)
    return lines


def shown_branch(info: RepositoryInfo) -> str:
    """The branch the row names, or "" for main and master."""
    if info.branch is None:
        return "detached"
    return "" if info.branch in _DEFAULT_BRANCHES else info.branch


def attention_rank(
    info: RepositoryInfo, sessions: RepoSessions | None = None, *, loading: bool = False
) -> int:
    if loading:
        return _LOADING_RANK
    if sessions is not None and sessions.waiting:
        return _WAITING_RANK
    if info.status in _SYNC_RANK:
        return _SYNC_RANK[info.status]
    if info.staged or info.unstaged:
        return _DIRTY_RANK
    if info.status is RepoStatus.UNKNOWN:
        return _UNKNOWN_RANK
    return _CLEAN_RANK


def needs_attention(info: RepositoryInfo, sessions: RepoSessions | None = None) -> bool:
    return attention_rank(info, sessions) < _UNKNOWN_RANK


def _fit(text: Text, width: int, *, right: bool = False) -> Text:
    text = text.copy()
    text.truncate(width, overflow="ellipsis")
    padding = " " * (width - text.cell_len)
    if right:
        return Text(padding) + text
    text.append(padding)
    return text


def _indent(grouped: bool) -> int:
    return _MARKER_WIDTH + (_CHILD_INDENT if grouped else 0)


def name_width(info: RepositoryInfo, *, grouped: bool, loading: bool = False) -> int:
    width = cell_len(info.name) + _indent(grouped)
    branch = "" if loading else shown_branch(info)
    return width + (_BRANCH_GAP + cell_len(branch) if branch else 0)


def resolve_repo_layout(
    names: Iterable[int], statuses: Iterable[int], screen_width: int, *, min_status: int = 0
) -> RepoLayout:
    """Size the columns for rows whose name and status texts are this wide.

    Sessions take what is left and, on a narrow screen, give way first.
    """
    total = (
        max(40, screen_width - _TABLE_CHROME_WIDTH) if screen_width > 0 else _FALLBACK_TOTAL_WIDTH
    )
    name = max(_MIN_NAME_WIDTH, min(_MAX_NAME_WIDTH, max(names, default=0)))
    status = max(_MIN_STATUS_WIDTH, min_status, min(_MAX_STATUS_WIDTH, max(statuses, default=0)))
    fixed = _UPDATED_WIDTH + _COL_GAP * 3
    while total - fixed - name - status < _MIN_SESSIONS_WIDTH:
        if status > _MIN_STATUS_WIDTH and status >= name // 2:
            status -= 1
        elif name > _MIN_NAME_WIDTH:
            name -= 1
        else:
            break
    sessions = max(0, total - fixed - name - status)
    return RepoLayout(name, _UPDATED_WIDTH, status, sessions)


def _join(cells: Sequence[Text]) -> Text:
    text = Text(" " * CELL_PADDING, no_wrap=True, overflow="ignore")
    for index, cell in enumerate(cells):
        if index:
            text.append(" " * _COL_GAP)
        text.append_text(cell)
    text.append(" " * CELL_PADDING)
    return text


def repo_header(layout: RepoLayout) -> Text:
    labels = (" " * _MARKER_WIDTH + _HEADERS[0], *_HEADERS[1:])
    widths = (layout.name, layout.updated, layout.status, layout.sessions)
    return _join([_fit(Text(label), width) for label, width in zip(labels, widths)])


def repo_row(
    info: RepositoryInfo,
    layout: RepoLayout,
    palette: TablePalette,
    *,
    grouped: bool,
    loading: bool = False,
    sessions: RepoSessions | None = None,
    now: float | None = None,
) -> Text:
    indent = " " * _indent(grouped)
    attention = not loading and needs_attention(info, sessions)
    name = Text(indent + info.name, style="bold" if attention else "")
    branch = "" if loading else shown_branch(info)
    if branch:
        name.append(" " * _BRANCH_GAP)
        name.append(branch, style=palette.muted if info.branch is None else palette.primary)
    if loading:
        updated = Text("")
    else:
        seconds = (time() if now is None else now) - (info.last_commit_timestamp or 0)
        updated = Text(
            updated_label(info.last_commit_timestamp, now),
            style=palette.muted if seconds >= _WEEK else "",
        )
    session_lines = sessions_lines(sessions, palette, layout.sessions)
    row = _join(
        (
            _fit(name, layout.name),
            _fit(updated, layout.updated),
            _fit(status_text(info, palette, loading=loading), layout.status),
            _fit(session_lines[0], layout.sessions),
        )
    )
    # Sessions that do not fit wrap inside their own column.
    indent = " " * (CELL_PADDING + layout.total - layout.sessions)
    for line in session_lines[1:]:
        row.append("\n" + indent)
        row.append_text(_fit(line, layout.sessions))
        row.append(" " * CELL_PADDING)
    return row


def row_height(row: Text) -> int:
    return row.plain.count("\n") + 1


def display_path(path: Path) -> str:
    try:
        return f"~/{path.relative_to(Path.home())}"
    except ValueError:
        return str(path)


def group_row(
    group: RepoGroup,
    infos: Sequence[RepositoryInfo],
    loading: set[Path],
    sessions: dict[Path, RepoSessions],
    layout: RepoLayout,
    palette: TablePalette,
    *,
    collapsed: bool,
    lead: bool = False,
) -> Text:
    """A group heading, on its own line after a blank one when *lead*.

    An open group shows only its name: its repositories are right below it.
    A folded one says how many it holds and what needs attention inside, so
    folding never hides work. A waiting session always shows.
    """
    line = Text(" " * CELL_PADDING, no_wrap=True, overflow="ellipsis")
    line.append(f"{_COLLAPSED if collapsed else _EXPANDED} ", style=palette.muted)
    line.append(group.name, style=f"bold {palette.primary}")

    details: list[tuple[str, str]] = []
    if collapsed:
        details.append(
            (f"{group.repo_count} {'repo' if group.repo_count == 1 else 'repos'}", palette.muted)
        )
        if any(info.path in loading for info in infos):
            details.append(("checking…", palette.muted))
        else:
            attention = sum(needs_attention(info, sessions.get(info.path)) for info in infos)
            if attention:
                verb = "needs" if attention == 1 else "need"
                details.append((f"{attention} {verb} attention", f"bold {palette.yellow}"))
    waiting = sum(sessions[info.path].waiting for info in infos if info.path in sessions)
    if waiting:
        details.append((f"● {waiting} waiting", f"bold {palette.yellow}"))
    for index, (text, style) in enumerate(details):
        line.append(" " * _COL_GAP if index == 0 else " · ", style=palette.muted)
        line.append(text, style=style)
    line.truncate(layout.cell_width, overflow="ellipsis", pad=True)
    if lead:
        return Text(" " * layout.cell_width + "\n", no_wrap=True) + line
    return line


def _sync_fact(info: RepositoryInfo, palette: TablePalette) -> Text:
    attention = f"bold {palette.yellow}"
    if info.status is RepoStatus.UNKNOWN:
        return Text(info.message or "unknown", style=palette.muted)
    text = Text()
    if info.ahead or info.status is RepoStatus.AHEAD:
        text.append(f"↑{info.ahead or ''} to push", style=attention)
    if info.behind or info.status is RepoStatus.BEHIND:
        if text:
            text.append(" · ", style=palette.muted)
        text.append(f"↓{info.behind or ''} to pull", style=attention)
    if not text:
        text.append(f"in step with origin/{info.branch}")
    if info.sync_stale:
        text.append("  (origin unreachable: last known)", style=palette.muted)
    return text


def _worktree_fact(info: RepositoryInfo, palette: TablePalette) -> Text:
    text = Text()
    if info.staged:
        text.append(f"{_count(info.staged_files)}staged", style=f"bold {palette.success}")
    if info.unstaged:
        if text:
            text.append(" · ", style=palette.muted)
        text.append(f"{_count(info.unstaged_files)}changed", style=f"bold {palette.yellow}")
    return text or Text("clean")


def _size_label(size: int) -> str:
    for unit, threshold in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if size >= threshold:
            return f"{size / threshold:.1f} {unit}"
    return f"{size} B"


def repo_facts(
    info: RepositoryInfo | None, sessions: RepoSessions | None, palette: TablePalette
) -> list[tuple[str, Text]]:
    """The git summary at the top of a repository's Info screen."""
    if info is None:
        return [("Status", Text("checking…", style=palette.muted))]
    facts = [
        ("Branch", Text(info.branch or "detached", style="bold")),
        ("Sync", _sync_fact(info, palette)),
        ("Worktree", _worktree_fact(info, palette)),
        ("Last commit", Text(info.last_updated or "-")),
    ]
    if info.size is not None:
        facts.append(("Tracked", Text(_size_label(info.size))))
    facts.append(
        ("Sessions", sessions_text(sessions, palette) or Text("none", style=palette.muted))
    )
    return facts


def group_facts(
    group: RepoGroup,
    infos: Sequence[RepositoryInfo],
    sessions: dict[Path, RepoSessions],
    palette: TablePalette,
) -> list[tuple[str, Text]]:
    attention = [info.name for info in infos if needs_attention(info, sessions.get(info.path))]
    live = sum(sessions[info.path].count for info in infos if info.path in sessions)
    return [
        ("Folder", Text(display_path(group.path))),
        ("Repositories", Text(group.repo_names)),
        (
            "Attention",
            Text(", ".join(attention), style=f"bold {palette.yellow}")
            if attention
            else Text("all clean"),
        ),
        ("Sessions", Text(f"{live} running") if live else Text("none", style=palette.muted)),
    ]
