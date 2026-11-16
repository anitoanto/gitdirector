import click
from rich.text import Text

from .. import paths
from ..config import Config
from ..repo import is_git_repository
from . import DANGER, MUTED, CommandError, confirm, console, count_noun, display_path


def register(cli: click.Group):
    @cli.command()
    @click.option("-y", "--yes", is_flag=True, help="Skip the confirmation prompt")
    def autoclean(yes: bool):
        """Clear the cache and stop tracking repositories that are gone

        Everything under ~/.gitdirector/cache is rebuilt on demand, so it is
        cleared without asking. A tracked path that is gone, or is no longer
        a git checkout, is a broken link; the command lists them and removes
        them after asking.
        """
        _clear_cache()
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


def _clear_cache() -> None:
    try:
        paths.clear_cache()
    except OSError as exc:
        raise CommandError(f"Failed to clear {display_path(paths.cache_dir())}: {exc}") from exc
    console.print(f"Cleared {display_path(paths.cache_dir())}.")
