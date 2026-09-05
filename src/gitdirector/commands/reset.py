import shutil
from pathlib import Path

import click

from ..config import Config
from . import console


def register(cli: click.Group):
    @cli.command()
    @click.option(
        "--yes",
        "-y",
        is_flag=True,
        help="Skip the confirmation prompt",
    )
    def reset(yes: bool):
        """Kill every session and panel and wipe ~/.gitdirector"""
        _reset(confirm=not yes)


def _reset(*, confirm: bool = True) -> None:
    config_dir = Path.home() / ".gitdirector"

    if confirm and not click.confirm(
        f"  This will kill every gd tmux session and permanently delete {config_dir}. Continue?",
        default=False,
    ):
        console.print()
        console.print("  [dim]Cancelled.[/dim]")
        console.print()
        return

    console.print()

    killed = _kill_all_sessions()
    if killed:
        console.print(f"  [green]Killed {len(killed)} session(s):[/green]")
        for name in killed:
            console.print(f"    [dim]-[/dim] {name}")
    else:
        console.print("  [dim]No active gd tmux sessions to kill.[/dim]")

    _wipe_config_dir(config_dir)

    config = _recreate_config()

    console.print()
    console.print(f"  [green]Wiped and recreated:[/green] {config.config_file}")
    console.print()


def _kill_all_sessions() -> list[str]:
    try:
        from ..integrations.tmux import kill_all_gd_sessions
    except ImportError:
        console.print("  [yellow]tmux integration unavailable; skipping session kill.[/yellow]")
        return []

    try:
        return kill_all_gd_sessions()
    except Exception as exc:
        console.print(f"  [yellow]Failed to kill some sessions: {exc}[/yellow]")
        return []


def _wipe_config_dir(config_dir: Path) -> None:
    if not config_dir.exists():
        console.print(f"  [dim]No existing {config_dir} to remove.[/dim]")
        return

    try:
        shutil.rmtree(config_dir)
    except OSError as exc:
        raise RuntimeError(f"Failed to remove {config_dir}: {exc}") from exc


def _recreate_config() -> Config:
    config = Config()
    config.clear()
    return config
