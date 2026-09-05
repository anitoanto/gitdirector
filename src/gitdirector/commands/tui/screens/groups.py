"""Modal screens related to repository groups."""

from __future__ import annotations

from pathlib import Path

from rich.markup import escape

from .session_actions import SessionActionMenuScreen, session_action_menu_css


class GroupActionMenuScreen(SessionActionMenuScreen):
    """Modal popup with session actions for the selected repository group."""

    CSS = session_action_menu_css("GroupActionMenuScreen")

    def __init__(
        self,
        group_name: str,
        group_path: Path,
        repo_count: int,
        repo_names: str,
    ) -> None:
        super().__init__(group_name, group_path)
        self.repo_count = repo_count
        self.repo_names = repo_names

    def _subtitle(self) -> str:
        repo_label = "repository" if self.repo_count == 1 else "repositories"
        return f"[dim]{self.repo_count} {repo_label}:[/dim] [$text-primary]{escape(self.repo_names)}[/]"
