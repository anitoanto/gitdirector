import click


def register(cli: click.Group):
    @cli.command(name="console")
    def console():
        """Open the interactive dashboard"""
        from ..launch_context import leave_launch_directory

        leave_launch_directory()
        # Imported here so the Textual stack is only loaded for the TUI,
        # not for every CLI invocation.
        from .tui.app import _run_console

        _run_console()
