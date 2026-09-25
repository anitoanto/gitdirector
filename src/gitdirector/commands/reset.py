import shutil
from pathlib import Path

import click
from rich.text import Text

from ..config import Config
from . import ATTENTION, MUTED, confirm, console, count_noun, display_path, error_console


def register(cli: click.Group):
    @cli.command()
    @click.option("-y", "--yes", is_flag=True, help="Skip the confirmation prompt")
    def reset(yes: bool):
        """Kill every session and wipe ~/.gitdirector

        Tracked repositories, panels, and settings are all lost; the
        repositories themselves are not touched.
        """
        config_dir = Path.home() / ".gitdirector"
        if not yes and not confirm(
            f"Kill every GitDirector session and delete {display_path(config_dir)}?",
            default=False,
        ):
            return

        killed = _kill_all_sessions()
        console.print(
            f"Killed {count_noun(len(killed), 'session')}" if killed else "No sessions to kill"
        )
        for name in killed:
            console.print(Text(f"  {name}", style=MUTED))
        _wipe_config_dir(config_dir)
        config = _recreate_config()
        console.print(f"Reset {display_path(config.config_dir)}")


def _kill_all_sessions() -> list[str]:
    try:
        from ..integrations.tmux import kill_all_gd_sessions

        return kill_all_gd_sessions()
    except Exception as exc:
        error_console.print(f"Could not kill every session: {exc}", style=ATTENTION)
        return []


def _wipe_config_dir(config_dir: Path) -> None:
    if not config_dir.exists():
        return
    try:
        shutil.rmtree(config_dir)
    except OSError as exc:
        raise RuntimeError(f"Failed to remove {config_dir}: {exc}") from exc


def _recreate_config() -> Config:
    config = Config()
    config.clear()
    return config
