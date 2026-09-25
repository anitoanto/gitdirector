import click

from ..manager import RepositoryManager
from . import CommandError, resolve_repository
from .completion import complete_repository_names, complete_session_names
from .sessions import AGENT_CHOICE, MODE_CHOICE, resolve_agent, start_session, tmux_errors


def _complete_target(ctx, param, incomplete):
    if incomplete.startswith("gd/"):
        return complete_session_names(ctx, param, incomplete)
    return complete_repository_names(ctx, param, incomplete)


def register(cli: click.Group):
    @cli.command()
    @click.argument("target", metavar="PATH|NAME|SESSION", shell_complete=_complete_target)
    @click.option("-a", "--agent", type=AGENT_CHOICE, help="Start an AI agent instead of a shell")
    @click.option("-m", "--mode", type=MODE_CHOICE, help="Claude Code permission mode [auto]")
    @click.option("-d", "--description", help="What the session is for")
    def cd(target: str, agent: str | None, mode: str | None, description: str | None):
        """Open a session in a repository, or rejoin one

        Starts a shell (or an AI agent with --agent) in a new tmux session for
        the repository and opens it beside the session sidebar, as the console
        does. Given a live session name (gd/...), opens that session instead.
        Detach with prefix d; the session keeps running.

        \b
        Examples:
          gitdirector cd web
          gitdirector cd web --agent claude --mode bypass
          gitdirector cd gd/web_x1y2z/claude-auto/1
        """
        from ..integrations.tmux import attach_tmux_session
        from ..integrations.tmux.core import _parse_gd_session_name, _session_exists
        from ..launch_context import leave_launch_directory

        spec = resolve_agent(agent, mode)
        if _parse_gd_session_name(target) is not None:
            if spec is not None or description is not None:
                raise click.UsageError("--agent, --mode and --description start a new session")
            if not _session_exists(target):
                raise CommandError(f"session {target!r} is not running")
            leave_launch_directory(["cd", target])
            with tmux_errors("Couldn't open the session"):
                attach_tmux_session(target)
            return

        repo_path = resolve_repository(target, RepositoryManager())
        # The path is resolved already, so the restart may drop the cwd.
        args = ["cd", str(repo_path)]
        for flag, value in (("--agent", agent), ("--mode", mode), ("--description", description)):
            if value is not None:
                args += [flag, value]
        leave_launch_directory(args)

        session_name = start_session(repo_path, agent=spec, mode=mode, description=description)
        try:
            with tmux_errors("Couldn't open the session"):
                attach_tmux_session(session_name, skip_config_sync=True)
        except BaseException:
            from ..integrations.tmux import kill_tmux_session

            kill_tmux_session(session_name)
            raise
