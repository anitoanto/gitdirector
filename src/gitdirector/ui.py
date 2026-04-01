from rich.text import Text
from textual.app import ComposeResult, RenderableType
from textual.containers import Container
from textual.reactive import reactive
from textual.widgets import DataTable, Static

from .repo import RepositoryInfo, RepoStatus

_STATUS_COLOR = {
    RepoStatus.UP_TO_DATE: "green",
    RepoStatus.BEHIND: "yellow",
    RepoStatus.AHEAD: "cyan",
    RepoStatus.DIVERGED: "red",
    RepoStatus.UNKNOWN: "bright_black",
}

_STATUS_LABEL = {
    RepoStatus.UP_TO_DATE: "up to date",
    RepoStatus.BEHIND: "behind",
    RepoStatus.AHEAD: "ahead",
    RepoStatus.DIVERGED: "diverged",
    RepoStatus.UNKNOWN: "unknown",
}


class StatusHeader(Static):
    DEFAULT_CSS = """
    StatusHeader {
        background: $surface;
        color: $text-muted;
        height: 2;
        padding: 0 2;
        border-bottom: solid $panel;
    }
    """

    def render(self) -> RenderableType:
        return Text("GITDIRECTOR", style="bold white")


class RepositoryTable(DataTable):
    DEFAULT_CSS = """
    RepositoryTable {
        border: none;
        height: 1fr;
        background: $surface;
    }
    """

    def __init__(self, repos: list[RepositoryInfo], *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.repos = repos

    _PATH_MAX_LEN = 50

    def on_mount(self) -> None:
        self.add_columns("REPOSITORY", "SYNC", "BRANCH", "CHANGES", "PATH")

        for repo in self.repos:
            color = _STATUS_COLOR.get(repo.status, "white")
            label = _STATUS_LABEL.get(repo.status, repo.status.value)
            full_path = str(repo.path)
            if len(full_path) > self._PATH_MAX_LEN:
                display_path = "…" + full_path[-(self._PATH_MAX_LEN - 1) :]
            else:
                display_path = full_path
            if repo.staged and repo.unstaged:
                changes = Text("staged+unstaged", style="yellow")
            elif repo.staged:
                changes = Text("staged", style="cyan")
            elif repo.unstaged:
                changes = Text("unstaged", style="yellow")
            else:
                changes = Text("—", style="bright_black")
            self.add_row(
                repo.name,
                Text(label, style=color),
                repo.branch or "—",
                changes,
                Text(display_path, style="dim"),
            )


class StatusView(Container):
    DEFAULT_CSS = """
    StatusView {
        height: 1fr;
        background: $surface;
    }
    """

    def compose(self) -> ComposeResult:
        yield StatusHeader()

    def update_status(self, repos: list[RepositoryInfo]) -> None:
        table = RepositoryTable(repos)
        self.mount(table)


class PullProgressView(Container):
    DEFAULT_CSS = """
    PullProgressView {
        height: 1fr;
        background: $surface;
        padding: 1 2;
    }
    """

    message = reactive("Pulling repositories...")

    def render(self) -> RenderableType:
        return Text(self.message, style="dim")
