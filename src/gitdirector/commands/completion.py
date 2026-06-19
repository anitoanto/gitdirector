import click
from click.shell_completion import CompletionItem, get_completion_class

from ..config import Config

SUPPORTED_SHELLS = ("bash", "zsh", "fish")


def _completion_var(prog_name: str) -> str:
    return f"_{prog_name.replace('-', '_').replace('.', '_').upper()}_COMPLETE"


def completion_source(cli: click.Group, shell: str, *, prog_name: str = "gitdirector") -> str:
    complete_cls = get_completion_class(shell)
    if complete_cls is None:
        raise click.ClickException(f"Unsupported shell: {shell}")
    return complete_cls(cli, {}, prog_name, _completion_var(prog_name)).source()


def complete_repository_names(
    _ctx: click.Context, _param: click.Parameter, incomplete: str
) -> list[CompletionItem]:
    try:
        repositories = Config().repositories
    except (OSError, RuntimeError, ValueError):
        return []

    prefix = incomplete.lower()
    return [
        CompletionItem(repo.name, help=str(repo))
        for repo in sorted(repositories, key=lambda path: (path.name.lower(), str(path)))
        if repo.name.lower().startswith(prefix)
    ]


def register(cli: click.Group):
    @cli.command()
    @click.argument("shell", type=click.Choice(SUPPORTED_SHELLS))
    def completion(shell: str):
        """Print shell completion setup for bash, zsh, or fish."""
        click.echo(completion_source(cli, shell), nl=False)
