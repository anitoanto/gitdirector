import click

from ..manager import RepositoryManager, describe_resolution_failure
from . import print_error
from .completion import complete_repository_names


def register(cli: click.Group):
    @cli.command()
    @click.argument("target", metavar="PATH|NAME", shell_complete=complete_repository_names)
    def cd(target: str):
        """Open a tmux session for a tracked repository

        PATH|NAME is a tracked repository's path or directory name. When two
        tracked repositories share a name, pass the path.
        """
        manager = RepositoryManager()
        repo_path, matches, path_attempted = manager.resolve_repository_target(target)
        if repo_path is None:
            print_error(describe_resolution_failure(target, matches, path_attempted))
            raise SystemExit(1)

        from ..integrations.tmux import open_in_tmux

        open_in_tmux(repo_path.name, repo_path)
