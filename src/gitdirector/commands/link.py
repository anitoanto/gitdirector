from pathlib import Path

import click
from rich.text import Text

from ..manager import RepositoryManager
from . import MUTED, SUCCESS, CommandError, console, count_noun, display_path, summary_line


def register(cli: click.Group):
    @cli.command()
    @click.argument("path", type=click.Path(file_okay=False, path_type=Path))
    @click.option("--discover", is_flag=True, help="Track every git repository found under PATH")
    def link(path: Path, discover: bool):
        """Track a repo, or all repos under a directory

        With --discover, PATH is walked recursively (honouring .gitignore
        files) and every git checkout found is tracked.
        """
        success, message, added, skipped = RepositoryManager().add_repository(
            path, discover=discover
        )
        if not success:
            raise CommandError(message)
        for repo in added:
            console.print(
                Text.assemble(
                    ("+ ", SUCCESS), (repo.name, "bold"), "  ", (display_path(repo), MUTED)
                ),
                soft_wrap=True,
            )
        if discover:
            console.print(
                summary_line(
                    f"Tracking {count_noun(len(added), 'new repository', 'new repositories')}"
                    if added
                    else message,
                    *([(f"{len(skipped)} already tracked", MUTED)] if skipped else []),
                )
            )
