import click
from click.shell_completion import CompletionItem, get_completion_class

from ..config import Config
from ..integrations.tmux import list_all_gd_sessions

SUPPORTED_SHELLS = ("bash", "zsh", "fish")


def _completion_var(prog_name: str) -> str:
    return f"_{prog_name.replace('-', '_').replace('.', '_').upper()}_COMPLETE"


_ZSH_COMPINIT_BOOTSTRAP = (
    "\n"
    "# Ensure ``compdef`` is available when this script is ``eval``'d in a\n"
    "# shell that has not yet loaded ``compinit``. Without this guard the\n"
    "# ``compdef`` call below fails with ``command not found: compdef``.\n"
    "if ! typeset -f compdef >/dev/null 2>&1; then\n"
    "    autoload -U +X compinit\n"
    "    compinit\n"
    "fi\n"
)


def _patch_zsh_source(source: str) -> str:
    """Inject a compinit bootstrap so ``eval`` works without prior setup.

    Click's zsh output ends with ``compdef ...`` which requires the
    ``compdef`` builtin provided by ``compinit``. When the script is
    eval'd in a fresh zsh that hasn't loaded ``compinit`` yet, that
    final call fails. Inserting a bootstrap right after the
    ``#compdef`` directive ensures ``compdef`` is available without
    changing behavior when the script is dropped into ``$fpath`` and
    autoloaded by ``compinit`` (in which case ``compdef`` already
    exists and the bootstrap is a no-op).
    """
    marker = "\n_gitdirector_completion() {"
    if marker not in source:
        return source
    return source.replace(marker, _ZSH_COMPINIT_BOOTSTRAP + "\n_gitdirector_completion() {", 1)


def completion_source(cli: click.Group, shell: str, *, prog_name: str = "gitdirector") -> str:
    complete_cls = get_completion_class(shell)
    if complete_cls is None:
        raise click.ClickException(f"Unsupported shell: {shell}")
    source = complete_cls(cli, {}, prog_name, _completion_var(prog_name)).source()
    if shell == "zsh":
        source = _patch_zsh_source(source)
    return source


def complete_repository_names(
    _ctx: click.Context, _param: click.Parameter, incomplete: str
) -> list[CompletionItem]:
    try:
        repositories = Config().repositories
    except (OSError, RuntimeError, ValueError):
        return []

    prefix = incomplete.lower()
    ordered = sorted(repositories, key=lambda path: (path.name.lower(), str(path)))
    name_counts: dict[str, int] = {}
    for repo in ordered:
        name_counts[repo.name] = name_counts.get(repo.name, 0) + 1

    items: list[CompletionItem] = []
    emitted_names: set[str] = set()
    for repo in ordered:
        name = repo.name
        # A shared basename is exactly the case where the commands demand a
        # full path, so completing only the name would offer a value that is
        # guaranteed to be rejected. Complete the paths instead.
        if name_counts[name] > 1:
            if str(repo).lower().startswith(prefix) or name.lower().startswith(prefix):
                items.append(CompletionItem(str(repo), help=f"{name} (ambiguous name)"))
            continue
        if not name.lower().startswith(prefix) or name in emitted_names:
            continue
        emitted_names.add(name)
        items.append(CompletionItem(name, help=str(repo)))
    return items


def complete_session_names(
    _ctx: click.Context, _param: click.Parameter, incomplete: str
) -> list[CompletionItem]:
    """Complete live ``gd/<repo>/<purpose>/<N>`` session names."""
    try:
        entries = list_all_gd_sessions()
    except Exception:
        return []
    prefix = incomplete.lower()
    return [
        CompletionItem(entry["session_name"], help=f"{entry['purpose']} in {entry['repo']}")
        for entry in entries
        if entry["session_name"].lower().startswith(prefix)
    ]


def register(cli: click.Group):
    @cli.command()
    @click.argument("shell", type=click.Choice(SUPPORTED_SHELLS))
    def completion(shell: str):
        """Print the shell completion script for bash, zsh, or fish

        \b
        Enable it with one of:
          eval "$(gitdirector completion bash)"
          eval "$(gitdirector completion zsh)"
          gitdirector completion fish | source
        """
        click.echo(completion_source(cli, shell), nl=False)
