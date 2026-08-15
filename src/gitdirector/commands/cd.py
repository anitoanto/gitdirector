import click

from ..manager import RepositoryManager
from . import console
from .completion import complete_repository_names


def register(cli: click.Group):
    @cli.command()
    @click.argument("target", metavar="PATH|NAME", shell_complete=complete_repository_names)
    def cd(target: str):
        """Open or switch to a tmux session rooted at a tracked repository.

        Accepts either a repository name or a path, matching ``unlink`` and
        ``gd-tmux``. A path is what disambiguates two tracked repositories
        that share a basename, so the "use the full path" hint below has to
        be something this command can actually act on.
        """
        manager = RepositoryManager()
        repo_path, matches, path_attempted = manager.resolve_repository_target(target)

        if repo_path is None:
            if path_attempted:
                console.print(f"\n  [red]No tracked repository at path: {target}[/red]\n")
            elif matches:
                paths_list = "\n".join(f"  {path}" for path in matches)
                console.print(
                    f"\n  [red]Multiple repositories named '{target}' — use the full path:[/red]\n"
                    f"{paths_list}\n"
                )
            else:
                console.print(f"\n  [red]No tracked repository named: {target}[/red]\n")
            raise SystemExit(1)

        try:
            from ..integrations.tmux import open_in_tmux
        except ImportError:
            console.print(
                "\n  [red]The tmux integration is unavailable for the cd command.[/red]\n"
                "  Reinstall gitdirector or check your installation.\n"
            )
            raise SystemExit(1)

        open_in_tmux(repo_path.name, repo_path)
