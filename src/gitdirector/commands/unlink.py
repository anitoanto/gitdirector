from pathlib import Path

import click
from rich.text import Text

from ..manager import RepositoryManager
from . import ATTENTION, MUTED, CommandError, console, display_path, resolve_repository
from .completion import complete_repository_names


def register(cli: click.Group):
    @cli.command()
    @click.argument("target", metavar="PATH|NAME", shell_complete=complete_repository_names)
    @click.option("--discover", is_flag=True, help="Stop tracking every repository under PATH")
    def unlink(target: str, discover: bool):
        """Stop tracking a repo, or all under a directory

        Nothing on disk is touched; the repository is only removed from the
        list GitDirector tracks.
        """
        manager = RepositoryManager()
        path = Path(target) if discover else resolve_repository(target, manager)
        success, message, removed = manager.remove_repository(path, discover=discover)
        if not success:
            raise CommandError(message)
        for repo in removed:
            console.print(
                Text.assemble(
                    ("- ", ATTENTION), (repo.name, "bold"), "  ", (display_path(repo), MUTED)
                ),
                soft_wrap=True,
            )
