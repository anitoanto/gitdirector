import click
from rich.text import Text

from ..config import Config
from ..repo import is_git_repository
from . import DANGER, MUTED, confirm, console, count_noun, display_path


def register(cli: click.Group):
    @cli.command()
    @click.option("-y", "--yes", is_flag=True, help="Skip the confirmation prompt")
    def autoclean(yes: bool):
        """Stop tracking repositories that are gone

        A tracked path that is gone, or is no longer a git checkout, is a
        broken link; the command lists them and removes them after asking.
        """
        config = Config()
        broken = [path for path in config.repositories if not is_git_repository(path)]
        if not broken:
            console.print("All tracked repositories exist.")
            return
        for path in broken:
            console.print(
                Text.assemble(("✗ ", DANGER), (display_path(path), MUTED)), soft_wrap=True
            )
        noun = count_noun(len(broken), "broken link")
        if not yes and not confirm(f"Stop tracking {noun}?", default=False):
            return
        config.remove_repositories(broken)
        console.print(f"Removed {noun}.")
