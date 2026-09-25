import click

from ..manager import RepositoryManager
from . import resolve_repository
from .completion import complete_repository_names
from .sessions import AGENT_CHOICE, MODE_CHOICE, resolve_agent, start_session


def register(cli: click.Group):
    @cli.command("gd-tmux")
    @click.argument("target", metavar="PATH|NAME", shell_complete=complete_repository_names)
    @click.argument("command", required=False)
    @click.option("-a", "--agent", type=AGENT_CHOICE, help="Start an AI agent instead of COMMAND")
    @click.option("-m", "--mode", type=MODE_CHOICE, help="Claude Code permission mode [auto]")
    @click.option("-d", "--description", help="What the session is for, shown in the Sessions tab")
    def gd_tmux(
        target: str,
        command: str | None,
        agent: str | None,
        mode: str | None,
        description: str | None,
    ):
        """Start a command or agent in a background session

        Prints the session name on stdout and returns at once; read and drive
        the session with gd-capture and gd-send. COMMAND runs through
        'sh -lc', so pass it as one quoted string. The session ends when its
        program exits, taking its output with it: redirect output to keep,
        e.g. "make test 2>&1 | tee /tmp/test.log".

        An agent started with --agent reports its status (running, waiting,
        idle) exactly, like one started from the console.

        \b
        Examples:
          gitdirector gd-tmux web "npm run dev" -d "Vite dev server"
          gitdirector gd-tmux ~/src/api --agent claude -d "Claude: fix auth tests"
        """
        spec = resolve_agent(agent, mode)
        if (command is None) == (spec is None):
            raise click.UsageError("pass either COMMAND or --agent")
        if command is not None and not command.strip():
            raise click.UsageError("COMMAND must not be empty")
        start_session(
            resolve_repository(target, RepositoryManager()),
            command=command,
            agent=spec,
            mode=mode,
            description=description,
            on_created=click.echo,
        )
