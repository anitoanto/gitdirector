import subprocess

import click

from .commands import (
    CONTEXT_SETTINGS,
    autoclean,
    capture,
    cd,
    completion,
    console_cmd,
    doctor,
    error_console,
    gd_send,
    gd_tmux,
    get_version,
    help,
    info,
    link,
    listt,
    pull,
    reset,
    schedule_update_notice,
    status,
    unlink,
)
from .commands.help import show_help

__all__ = ["cli", "main"]

# Commands whose output is consumed by scripts, that report versions
# themselves, or that wipe ~/.gitdirector (the check's cache lives there, so
# a concurrent check would race the wipe); the update notice stays out of
# their way.
_NO_UPDATE_NOTICE = frozenset({"completion", "doctor", "console", "reset"})


class _HelpGroup(click.Group):
    def format_help(self, ctx, _formatter):
        show_help(self)


@click.group(cls=_HelpGroup, context_settings=CONTEXT_SETTINGS, invoke_without_command=True)
@click.version_option(
    get_version(), "-V", "--version", prog_name="gitdirector", message="%(prog)s %(version)s"
)
@click.pass_context
def cli(ctx):
    """Manage many git repositories and agent sessions from one place."""
    if ctx.invoked_subcommand is None:
        show_help(cli)
        return
    if not ctx.resilient_parsing and ctx.invoked_subcommand not in _NO_UPDATE_NOTICE:
        schedule_update_notice(ctx)


for module in (
    link,
    unlink,
    listt,
    status,
    pull,
    cd,
    console_cmd,
    info,
    doctor,
    autoclean,
    reset,
    gd_tmux,
    capture,
    gd_send,
    completion,
    help,
):
    module.register(cli)


def main():
    try:
        cli()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        error_console.print(f"Error: {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
