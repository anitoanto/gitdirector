import click
from rich.text import Text

from . import (
    ATTENTION,
    MUTED,
    SUCCESS,
    console,
    count_noun,
    print_json,
    repository_json,
    summary_line,
)
from .listt import gather_statuses


def register(cli: click.Group):
    @cli.command()
    @click.option("--json", "as_json", is_flag=True, help="Print JSON instead of text")
    def status(as_json: bool):
        """Show repositories with uncommitted changes

        Local only: nothing is fetched. Untracked files count as changed.
        """
        results = gather_statuses(fetch=False, include_size=False)
        dirty = [info for info in results if info.staged or info.unstaged]
        if as_json:
            print_json([repository_json(info) for info in dirty])
            return
        if not results:
            console.print("No repositories tracked. Add one with: gitdirector link PATH")
            return

        for info in dirty:
            console.print(Text.assemble((info.name, "bold"), "  ", (info.branch or "-", MUTED)))
            for path in info.staged_files or ():
                console.print(Text.assemble("  ", ("staged   ", SUCCESS), path), soft_wrap=True)
            for path in info.unstaged_files or ():
                console.print(Text.assemble("  ", ("changed  ", ATTENTION), path), soft_wrap=True)
            console.print()

        clean = len(results) - len(dirty)
        console.print(
            summary_line(
                count_noun(len(results), "repository", "repositories"),
                (f"{clean} clean", MUTED),
                *([(f"{len(dirty)} with changes", ATTENTION)] if dirty else []),
            )
        )
