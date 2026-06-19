import subprocess

import click

from .commands import (
    _changes_text,
    _format_size,
    _path_text,
    _status_text,
    autoclean,
    capture,
    cd,
    completion,
    console,
    gd_send,
    gd_tmux,
    help,
    info,
    link,
    listt,
    print_update_notice,
    pull,
    status,
    tui,
    unlink,
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
    def format_help(self, ctx, _formatter):
        show_help()


@click.group(cls=_HelpGroup, invoke_without_command=True)
@click.pass_context
def cli(ctx):
    if not ctx.resilient_parsing and ctx.invoked_subcommand != "completion":
        print_update_notice()
    if ctx.invoked_subcommand is None:
        show_help()


link.register(cli)
unlink.register(cli)
listt.register(cli)
status.register(cli)
pull.register(cli)
cd.register(cli)
help.register(cli)
tui.register(cli)
autoclean.register(cli)
info.register(cli)
gd_tmux.register(cli)
capture.register(cli)
gd_send.register(cli)
completion.register(cli)


def main():
    try:
        cli()
    except (
        click.ClickException,
        OSError,
        RuntimeError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        console.print(f"\n  [red]Error:[/red] {str(exc)}\n")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
