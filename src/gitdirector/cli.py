import click

from .commands import (
    _changes_text,
    _format_size,
    _path_text,
    _status_text,
    add,
    console,
    help,
    listt,
    pull,
    remove,
    status,
)
from .commands.help import show_help

__all__ = [
    "_changes_text",
    "_format_size",
    "_path_text",
    "_status_text",
    "cli",
    "main",
]


class _HelpGroup(click.Group):
    def format_help(self, ctx, formatter):
        show_help()


@click.group(cls=_HelpGroup, invoke_without_command=True)
@click.pass_context
def cli(ctx):
    if ctx.invoked_subcommand is None:
        show_help()


add.register(cli)
remove.register(cli)
listt.register(cli)
status.register(cli)
pull.register(cli)
help.register(cli)


def main():
    try:
        cli()
    except Exception as e:
        console.print(f"\n  [red]Error:[/red] {str(e)}\n")
        raise SystemExit(1)


if __name__ == "__main__":
    main()


def main():
    try:
        cli()
    except Exception as e:
        console.print(f"\n  [red]Error:[/red] {str(e)}\n")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
