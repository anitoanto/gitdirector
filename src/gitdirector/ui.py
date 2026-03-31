from rich.text import Text
from textual.app import ComposeResult, RenderableType
from textual.containers import Container
from textual.reactive import reactive
from textual.widgets import DataTable, Static

from .repo import RepositoryInfo, RepoStatus


class StatusHeader(Static):
    DEFAULT_CSS = """
    StatusHeader {
        background: $primary;
        color: $text;
        height: 3;
        padding: 1 2;
        border-bottom: solid $accent;
    }
    """

    def render(self) -> RenderableType:
        return Text("GitDirector - Repository Manager", style="bold cyan")


class RepositoryTable(DataTable):
    DEFAULT_CSS = """
    RepositoryTable {
        border: solid $accent;
        height: 1fr;
    }
    """

    def __init__(self, repos: list[RepositoryInfo], *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.repos = repos

    def on_mount(self) -> None:
        self.add_columns("Repository", "Status", "Branch", "Message")

        for repo in self.repos:
            status_style = self._get_status_style(repo.status)
            self.add_row(
                repo.name,
                Text(repo.status.value, style=status_style),
                repo.branch or "N/A",
                repo.message,
            )

    @staticmethod
    def _get_status_style(status: RepoStatus) -> str:
        styles = {
            RepoStatus.UP_TO_DATE: "green",
            RepoStatus.BEHIND: "yellow",
            RepoStatus.AHEAD: "cyan",
            RepoStatus.DIVERGED: "red",
            RepoStatus.UNKNOWN: "dim white",
        }
        return styles.get(status, "white")


class StatusView(Container):
    DEFAULT_CSS = """
    StatusView {
        height: 1fr;
        border: solid $accent;
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
        border: solid $accent;
        padding: 1 2;
    }
    """

    message = reactive("Pulling repositories...")

    def render(self) -> RenderableType:
        return Text(self.message)
